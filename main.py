import os
import json
import requests
import logging
from flask import Flask, request, jsonify, render_template
import google.generativeai as genai
import base64
import redis


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
wa_token = os.environ.get("WA_TOKEN")  # WhatsApp API Key
WA_TOKEN = wa_token
phone_id = os.environ.get("PHONE_ID")
genai.configure(api_key=os.environ.get("GEN_API"))    # Gemini API Key
owner_phone = os.environ.get("OWNER_PHONE")
GRAPH_API_BASE = "https://graph.facebook.com/v19.0"

# ==================== Upstash Redis Config ====================
redis_client = redis.from_url("redis://default:facc3ff3991744eabd48508929d0215c@gusc1-peaceful-ape-31692.upstash.io:31692", decode_responses=True)
REDIS_URL = os.environ.get("UPSTASH_REDIS_REST_URL")
REDIS_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN")

if not REDIS_URL or not REDIS_TOKEN:
    raise EnvironmentError("UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN must be set.")

class UpstashRedisClient:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    def set(self, key, value, ex=None):
        url = f"{self.base_url}/set/{key}"
        payload = {"value": value}
        if ex is not None:
            payload["ex"] = ex  # Expiry in seconds
        response = requests.post(url, headers=self.headers, data=json.dumps(payload))
        response.raise_for_status()
        return response.json()

    def get(self, key):
        url = f"{self.base_url}/get/{key}"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        result = response.json()
        return result.get("result")

# Initialize Redis client
redis_client = UpstashRedisClient(REDIS_URL, REDIS_TOKEN)

def validate_whatsapp_number(number):
    """Validate WhatsApp number format"""
    if not number or not isinstance(number, str):
        return False
    clean_number = ''.join(c for c in number if c.isdigit())
    return len(clean_number) >= 10 and len(clean_number) <= 15

def save_user_state(user_id, state, expiry_seconds=60):
    """Save user state to Redis with validation"""
    if not validate_whatsapp_number(user_id):
        logger.error(f"Invalid user_id provided: {user_id}")
        return False
    if not state or not isinstance(state, dict):
        logger.error(f"Invalid state provided: {state}")
        return False
    
    try:
        redis_client.set(f"user_state:{user_id}", json.dumps(state), ex=expiry_seconds)
        logger.info(f"Successfully saved state for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Error saving user state for {user_id}: {str(e)}")
        return False

def get_user_state(user_id):
    """Get user state from Redis with validation"""
    if not validate_whatsapp_number(user_id):
        logger.error(f"Invalid user_id in get_user_state: {user_id}")
        return {}
    
    try:
        result = redis_client.get(f"user_state:{user_id}")
        if result:
            try:
                return json.loads(result)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to decode state for {user_id}: {str(e)}")
        return {}
    except Exception as e:
        logger.error(f"Error getting state for {user_id}: {str(e)}")
        return {}

def check_redis_connection():
    """Check if Redis connection is working"""
    try:
        redis_client.get("test_key")
        return True
    except Exception as e:
        logger.error(f"Redis connection failed: {str(e)}")
        return False

# ==================== Messaging Logic ====================

def advance(sender, user_state, next_step, message):
    if user_state.get("step") == next_step:
        return None, user_state  # Don't change or resend
    user_state['step'] = next_step
    return message, user_state

    
class User:
    def __init__(self, phone_number):
        self.phone_number = phone_number
        self.language = "English"
        self.quote_data = {}
        self.booking_data = {}
        self.offer_data = {}

    def to_dict(self):
        return {
            "phone_number": self.phone_number,
            "language": self.language,
            "quote_data": self.quote_data,
            "booking_data": self.booking_data,
            "offer_data": self.offer_data
        }

    @classmethod
    def from_dict(cls, data):
        user = cls(data.get("phone_number"))
        user.language = data.get("language", "English")
        user.quote_data = data.get("quote_data", {})
        user.booking_data = data.get("booking_data", {})
        user.offer_data = data.get("offer_data", {})
        return user

    
    def handle_approve_manual(prompt, user_data, phone_id):
        user = User.from_dict(user_data.get('user', {}))
        prompt = prompt.lower().strip()
        
        if prompt in ["boys", "girls", "mixed"]:
            user.booking_data["gender"] = prompt
            update_user_state(user_data['sender'], {
                "step": "collect_city",
                "user": user.to_dict()
            })
            send("Which city is your property located in?", user_data['sender'], phone_id)
            return {"step": "collect_city", "user": user.to_dict(), "sender": user_data['sender']}
        else:
            send("Please reply with 'boys', 'girls', or 'mixed'.", user_data['sender'], phone_id)
            return {"step": "approve_manual", "user": user.to_dict(), "sender": user_data['sender']}
    
    
    def update_user_state(sender, user_state):
        # Convert user_state dict to JSON string and save it
        redis_client.set(sender, json.dumps(user_state), ex=60)  # expire after 1 min
    
    def get_user_state(sender):
        data = redis_client.get(sender)
        if data:
            return json.loads(data)
        return {}  # default if no state stored yet
    
    

# ==================== Message Execution ====================

# For text messages, extract the text
def handle_text_message(message, sender, user_state):
    if message.get("type") != "text":
        return None, user_state

    msg = message.get("text", {}).get("body", "").strip().lower()
    step = user_state.get("step", "start")

    if step == "start":
        if "student" in msg:
            reply, user_state = advance(
                sender,
                user_state,
                "student_redirect",
                "Please use the student app to check hostel availability: https://playstore.com/xyz"
            )
            return reply, user_state

        elif "landlord" in msg:
            reply, user_state = advance(
                sender,
                user_state,
                "get_whatsapp_verification",
                "OK. Please send a screenshot of your WhatsApp username with your contact name for verification."
            )
            return reply, user_state

        else:
            reply, user_state = advance(
                sender,
                user_state,
                "start",
                "Hello! Are you a *student* or a *landlord*? Please reply with one."
            )
            return reply, user_state

    elif step == "get_whatsapp_verification":
        reply = "Please send a screenshot of your WhatsApp username with your contact name for verification."
        return reply, user_state

    elif step == "approve_manual":
        if msg in ["boys", "girls", "mixed"]:
            user_state["house_type"] = msg
            reply = "Do you have a *cat*? Please reply *yes* or *no*."
            user_state["step"] = "ask_cat_owner"
        else:
            reply = "Please reply with *boys*, *girls*, or *mixed*."

        send(reply, sender, phone_id)
        update_user_state(sender, user_state)
        return jsonify({"status": "ok"}), 200

    elif step == "ask_cat_owner":
        if msg in ["yes", "no"]:
            user_state["has_cat"] = msg
            reply = "Do you have a vacancy? Reply *yes* or *no*."
            user_state["step"] = "ask_availability"
        else:
            reply = "Do you have a cat? Please reply *yes* or *no*."

        send(reply, sender, phone_id)
        update_user_state(sender, user_state)
        return jsonify({"status": "ok"}), 200

    elif step == "ask_availability":
        if msg == "no":
            reply = "OK thanks. Whenever you have vacancies, don’t hesitate to say 'Hi!'"
            user_state["step"] = "end"
        elif msg == "yes":
            reply = "How many *boys* or *girls* do you need accommodation for in *single rooms*? (Enter number only)"
            user_state["step"] = "ask_room_type"
        else:
            reply = "Do you have a vacancy? Please reply *yes* or *no*."

        send(reply, sender, phone_id)
        update_user_state(sender, user_state)
        return jsonify({"status": "ok"}), 200

    elif step == "ask_room_type":
        if msg.isdigit():
            user_state["room_single"] = int(msg)
            reply = "Please confirm your rent for a single room (e.g. 130)."
            user_state["step"] = "confirm_single"
        else:
            reply = "Please enter the number of students needing single rooms (number only)."

        send(reply, sender, phone_id)
        update_user_state(sender, user_state)
        return jsonify({"status": "ok"}), 200

    elif step == "confirm_single":
        try:
            rent_single = float(msg)
            user_state["rent_single"] = rent_single
            reply = "How many students need 2-sharing rooms? (Enter number only)"
            user_state["step"] = "ask_2_sharing"
        except ValueError:
            reply = "Please enter the rent as a number (e.g. 130)."

        send(reply, sender, phone_id)
        update_user_state(sender, user_state)
        return jsonify({"status": "ok"}), 200

    elif step == "ask_2_sharing":
        if msg.isdigit():
            user_state["room_2_sharing"] = int(msg)
            reply = "Please confirm your rent for 2-sharing rooms (e.g. 80)."
            user_state["step"] = "confirm_2_sharing"
        else:
            reply = "Please enter number of students needing 2-sharing rooms (number only)."

        send(reply, sender, phone_id)
        update_user_state(sender, user_state)
        return jsonify({"status": "ok"}), 200

    elif step == "confirm_2_sharing":
        try:
            rent_2_sharing = float(msg)
            user_state["rent_2_sharing"] = rent_2_sharing
            reply = "How many students need 3-sharing rooms? (Enter number only)"
            user_state["step"] = "ask_3_sharing"
        except ValueError:
            reply = "Please enter the rent as a number (e.g. 80)."

        send(reply, sender, phone_id)
        update_user_state(sender, user_state)
        return jsonify({"status": "ok"}), 200

    elif step == "ask_3_sharing":
        if msg.isdigit():
            user_state["room_3_sharing"] = int(msg)
            reply = "Please confirm your rent for 3-sharing rooms (e.g. 60)."
            user_state["step"] = "confirm_3_sharing"
        else:
            reply = "Please enter number of students needing 3-sharing rooms (number only)."

        send(reply, sender, phone_id)
        update_user_state(sender, user_state)
        return jsonify({"status": "ok"}), 200

    elif step == "confirm_3_sharing":
        try:
            rent_3_sharing = float(msg)
            user_state["rent_3_sharing"] = rent_3_sharing
            reply = "What age group are the students? (e.g. 18-22)"
            user_state["step"] = "ask_student_age"
        except ValueError:
            reply = "Please enter the rent as a number (e.g. 60)."

        send(reply, sender, phone_id)
        update_user_state(sender, user_state)
        return jsonify({"status": "ok"}), 200

    elif step == "ask_student_age":
        user_state["student_age"] = msg
        reply = "Thank you. Please confirm your listing by typing *confirm* or type *cancel* to abort."
        user_state["step"] = "confirm_listing"

        send(reply, sender, phone_id)
        update_user_state(sender, user_state)
        return jsonify({"status": "ok"}), 200

    elif step == "confirm_listing":
        if msg == "confirm":
            reply = "Thank you! Your listing will be published soon. Expect calls from students if you have vacancies."
            user_state["step"] = "end"
            save_user_state(sender, user_state)
        elif msg == "cancel":
            reply = "Your listing was cancelled. Type 'Hi' to start over."
            user_state["step"] = "end"
            save_user_state(sender, user_state)
        else:
            reply = "Please type *confirm* to publish your listing or *cancel* to abort."

        send(reply, sender, phone_id)
        update_user_state(sender, user_state)
        return jsonify({"status": "ok"}), 200

    elif step == "end":
        if msg == "hi":
            reply = "Hello! Are you a *student* or a *landlord*? Please reply with one."
            user_state["step"] = "start"
            save_user_state(sender, user_state)
        else:
            reply = "Thank you for contacting us. Type 'Hi' if you want to start again."

        send(reply, sender, phone_id)
        update_user_state(sender, user_state)
        return jsonify({"status": "ok"}), 200

    # Fallback
    reply = "Sorry, I did not understand that. Please try again."
    send(reply, sender, phone_id)
    update_user_state(sender, user_state)
    return jsonify({"status": "ok"}), 200
        
def handle_message(message, sender, phone_id):
    user_state = get_user_state(sender) or {}
    step = user_state.get("step", "start")

    if message.get("type") == "image" and "image" in message:
        media_id = message["image"].get("id")
        if not media_id:
            logger.error("Image ID missing")
            return jsonify({"status": "error", "message": "Missing image ID"}), 400

        logger.info("Image message received")
        logger.info(f"Image media ID: {media_id}")
        logger.info(f"Image received from: {sender}")

        # Ensure user data exists
        if 'user' not in user_state:
            user_state['user'] = User(sender).to_dict()
        user_state['sender'] = sender

        # Store image info in Redis (Upstash)
        image_data = {
            "media_id": media_id,
            "type": "image",
            "timestamp": str(datetime.utcnow()),
        }

        # Save image metadata to Redis under user's key
        redis_key = f"user:{sender}:image"
        redis_client.hset(redis_key, mapping=image_data)
        redis_client.expire(redis_key, 86400)  # Optional: expire after 24 hours

        # Progress to approval step if not already there
        if user_state.get("step") != "approve_manual":
            name = user_state['user'].get("name", "")
            send(
                f"Thanks {name or 'there'}. Approval will be done manually for security reasons.\n\n"
                "Now let’s collect house details.\n\n"
                "Do you have accommodation for *boys*, *girls*, or *mixed*?",
                sender,
                phone_id
            )
            user_state["step"] = "approve_manual"
            update_user_state(sender, user_state)

        return jsonify({"status": "ok"}), 200           
def send(message, recipient, phone_id):
    """Send message via WhatsApp API with validation"""
    if not all([message, recipient, phone_id]):
        logger.error("Missing required parameters for send()")
        return False

    if not validate_whatsapp_number(recipient):
        logger.error(f"Invalid recipient number: {recipient}")
        return False

    url = f"https://graph.facebook.com/v13.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",  # Use WA_TOKEN (consistent with webhook handler)
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "text",
        "text": {"body": message}
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        logger.info(f"Message sent to {recipient}: {response.text}")
        return True

    
    except Exception as e:
        logger.error(f"Error sending message to {recipient}: {str(e)}")
        return False



# ==================== Flask Webhook Configuration ====================
app = Flask(__name__)

@app.route("/", methods=["GET"])
def index():
    return render_template("connected.html")
@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == "BOT":
            logger.info("Webhook verification successful.")
            return challenge, 200
        logger.warning("Webhook verification failed.")
        return "Failed", 403

    elif request.method == "POST":
        try:
            data = request.get_json()
            logger.info(f"Incoming webhook data: {json.dumps(data, indent=2)}")

            entry = data.get("entry", [])[0]
            changes = entry.get("changes", [])[0]
            value = changes.get("value", {})
            messages = value.get("messages", [])
            if not messages:
                logger.info("No valid messages in payload")
                return jsonify({"status": "ok", "message": "No messages"}), 200

            message = messages[0]
            sender = message.get("from")
            if not sender or not validate_whatsapp_number(sender):
                logger.error(f"Invalid sender ID: {sender}")
                return jsonify({"status": "error", "message": "Invalid sender"}), 400

            name = None
            if 'contacts' in value and value['contacts']:
                contact = value['contacts'][0]
                name = contact.get('profile', {}).get('name')

            user_state = get_user_state(sender) or {}
            user_state["user_id"] = sender

            return jsonify({"status": "ok"}), 200

        except Exception as e:
            logger.exception("Error handling webhook POST")
            return jsonify({"status": "error", "message": str(e)}), 500


# ==================== Main Execution ====================
if __name__ == "__main__":
    if not check_redis_connection():
        logger.error("Failed to connect to Redis - check configuration")
    else:
        logger.info("Redis connection successful")
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
