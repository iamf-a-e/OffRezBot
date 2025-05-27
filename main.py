import os
import json
import requests
import logging
from flask import Flask, request, jsonify, render_template

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
wa_token = os.environ.get("WA_TOKEN")  # WhatsApp API Key
phone_id = os.environ.get("PHONE_ID")
gen_api = os.environ.get("GEN_API")    # Gemini API Key
owner_phone = os.environ.get("OWNER_PHONE")

# ==================== Upstash Redis Config ====================
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

def message_handler(message, user_state):
    # Detect if returning user wants to restart
    if message.strip().lower() == "hi" and user_state.get("step") == "end":
        return (
            "Welcome back! What would you like to do today?\n"
            "1. Post a new vacancy\n"
            "2. Update existing listing\n"
            "3. Contact placement team",
            {"step": "returning_user_menu"}
        )

    msg = message.strip().lower()
    step = user_state.get("step", "start")

    def advance(new_step, response):
        user_state["step"] = new_step
        return response, user_state

    # Step 0: Introduction
    if step == "start":
        return advance("ask_user_type", "Hello, I’m the OffRez accommodation assistant. Are you a *student* or a *landlord*?")

    # Step 1: Identify user type
    if step == "ask_user_type":
        if msg == "landlord":
            return advance("get_whatsapp_verification", "OK. Please send a screenshot of your WhatsApp username with contact name back for verification.")
        elif msg == "student":
            return advance("student_redirect", "Please use the student app to check student hostel availability: https://playstore.com/xyz")
        else:
            return advance("ask_user_type", "Please reply with either *student* or *landlord*.")

    # Step 2: After verification, collect house attributes
    if step == "get_whatsapp_verification":
       return advance(
            "approve_manual",
            "Approval will be done manually for security reasons. Now let’s collect house details.\n"
            "Do you have accommodation for *boys*, *girls*, or *mixed*?"
        )

    # Step 3: Gender type
    if step == "approve_manual":
        if msg in ["boys", "girls", "mixed"]:
            user_state["house_type"] = msg
            return advance("ask_cat_owner", "Do you have a *cat*?")
        else:
            return advance("approve_manual", "Please answer with *boys*, *girls*, or *mixed*.")

    # Step 4: Ask about cat
    if step == "ask_cat_owner":
        if msg in ["yes", "no"]:
            user_state["has_cat"] = msg
            return advance("ask_availability", "Do you have a vacancy?")
        else:
            return advance("ask_cat_owner", "Do you have a *cat*? Please reply *yes* or *no*.")

    # Step 5: Availability
    if step == "ask_availability":
        if msg == "no":
            return advance("end", "OK thanks. Whenever you have vacancies, don’t hesitate to say 'Hi!'")
        elif msg == "yes":
            return advance("ask_room_type", "How many *boys* or *girls* do you need accommodation for in *single rooms*? (reply with number only)")
        else:
            return advance("ask_availability", "Do you have a vacancy? Please reply *yes* or *no*.")

    # Step 6: Capture room info recursively
    if step.startswith("ask_room_type"):
        if msg.isdigit():
            user_state["room_count"] = int(msg)
            return advance("confirm_single", f"Confirm your rent for single room (e.g. 1 is $130):")
        else:
            return advance("ask_room_type", "Please enter a number for how many students need single rooms.")

    if step == "confirm_single":
        if msg.replace(".", "").isdigit():
            user_state["rent_single"] = float(msg)
            return advance("ask_2_sharing", "How many students need 2-sharing rooms?")
        else:
            return advance("confirm_single", "Please reply with rent in numbers only (e.g. 130).")

    if step == "ask_2_sharing":
        if msg.isdigit():
            user_state["2_sharing"] = int(msg)
            return advance("confirm_2_sharing", "Confirm your rent for 2-sharing (e.g. 2 is $80):")
        else:
            return advance("ask_2_sharing", "Please enter number of students for 2-sharing.")

    if step == "confirm_2_sharing":
        if msg.replace(".", "").isdigit():
            user_state["rent_2_sharing"] = float(msg)
            return advance("ask_3_sharing", "How many students need 3-sharing rooms?")
        else:
            return advance("confirm_2_sharing", "Please reply with rent in numbers only (e.g. 80).")

    if step == "ask_3_sharing":
        if msg.isdigit():
            user_state["3_sharing"] = int(msg)
            return advance("confirm_3_sharing", "Confirm your rent for 3-sharing (e.g. 3 is $60):")
        else:
            return advance("ask_3_sharing", "Please enter number of students for 3-sharing.")

    if step == "confirm_3_sharing":
        if msg.replace(".", "").isdigit():
            user_state["rent_3_sharing"] = float(msg)
            return advance("ask_4_sharing", "How many students need 4-sharing rooms?")
        else:
            return advance("confirm_3_sharing", "Please reply with rent in numbers only (e.g. 60).")

    if step == "ask_4_sharing":
        if msg.isdigit():
            user_state["4_sharing"] = int(msg)
            return advance("confirm_4_sharing", "Confirm your rent for 4-sharing (e.g. 4 is $70):")
        else:
            return advance("ask_4_sharing", "Please enter number of students for 4-sharing.")

    if step == "confirm_4_sharing":
        if msg.replace(".", "").isdigit():
            user_state["rent_4_sharing"] = float(msg)
            return advance("end", "Thank you. I'm not a real machine, will get back to you. Whenever you need more students, just type 'Hi'!")
        else:
            return advance("confirm_4_sharing", "Please reply with rent in numbers only (e.g. 70).")


    if step == "returning_user_menu":
        if msg in ["1", "post a new vacancy"]:
            return advance("ask_room_type", "Let’s post a new vacancy.\nHow many *boys* or *girls* do you need accommodation for in *single rooms*?")
        elif msg in ["2", "update existing listing"]:
            return advance("update_details", "What detail would you like to update? (e.g. rent, capacity, type)")
        elif msg in ["3", "contact placement team"]:
            return advance("contact_team", "Our placement team will reach out shortly. Is there anything specific you’d like us to know?")
        else:
            return advance("returning_user_menu", "Please reply with 1, 2 or 3 to select an option.")

    if step == "update_details":
        user_state["update_note"] = msg
        return advance("end", "Thanks! We've noted your update request. Our team will contact you if needed.")

    if step == "contact_team":
        user_state["message_to_team"] = msg
        return advance("end", "Thanks! We've passed your message to the placement team.")

    if step == "end":
        return "You've reached the end of the conversation flow. Type 'Hi' to start again.", user_state

        # Detect if returning user wants to restart
    if message.strip().lower() == "hi" and user_state.get("step") == "end":
        return ("Welcome back! What would you like to do today?\n"
                "1. Post a new vacancy\n"
                "2. Update existing listing\n"
                "3. Contact placement team"), {"step": "returning_user_menu"}

    msg = message.strip().lower()
    step = user_state.get("step", "start")

    def advance(new_step, response=None):
        user_state["step"] = new_step
        return response, user_state

    # Handle landlord responses to vacancy confirmations
    if user_state.get("last_prompt") == "awaiting_landlord_info":
        if "yes" in msg:
            return advance("end", "Great! Expect the student's call/message soon. 📞")
        elif "no" in msg:
            return advance("end", "Ok thanks")
        elif "fully occupied" in msg:
            return advance("end", "Ok, whenever you need students just type the word 'Hie' 👋")

    # Fallback
    return "I didn’t get that. Please try again.", user_state


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
        data = request.get_json()
        logger.info(f"Incoming webhook data: {json.dumps(data, indent=2)}")

        try:
            # Validate webhook payload structure
            if not data or not isinstance(data, dict):
                logger.error("Invalid webhook payload")
                return jsonify({"status": "error", "message": "Invalid payload"}), 400

            entry = data.get("entry", [])
            if not isinstance(entry, list) or not entry:
                logger.error("Missing or invalid entry in payload")
                return jsonify({"status": "error", "message": "Invalid entry"}), 400

            changes = entry[0].get("changes", [])
            if not isinstance(changes, list) or not changes:
                logger.error("Missing or invalid changes in payload")
                return jsonify({"status": "error", "message": "Invalid changes"}), 400

            value = changes[0].get("value", {})
            if not isinstance(value, dict):
                logger.error("Missing or invalid value in payload")
                return jsonify({"status": "error", "message": "Invalid value"}), 400

            messages = value.get("messages", [])
            if not isinstance(messages, list) or not messages:
                logger.info("No valid messages in payload")
                return jsonify({"status": "ok", "message": "No messages"}), 200

            message = messages[0]
            if not isinstance(message, dict):
                logger.error("Invalid message format")
                return jsonify({"status": "error", "message": "Invalid message"}), 400

            # Extract sender and validate
            sender = message.get("from")
            if not validate_whatsapp_number(sender):
                logger.error(f"Invalid sender ID: {sender}")
                return jsonify({"status": "error", "message": "Invalid sender"}), 400

            # Process text message
            text_obj = message.get("text", {})
            if not isinstance(text_obj, dict) or "body" not in text_obj:
                logger.info("Received non-text message")
                return jsonify({"status": "ok"}), 200

            prompt = text_obj["body"].strip()
            logger.info(f"Processing message from {sender}: '{prompt}'")
            
            # Get or initialize user state
            user_state = get_user_state(sender) or {}
            
            # Handle message and get response
            response, new_state = message_handler(prompt, user_state)
            logger.debug(f"Generated response: {response}")
            
            # Save updated state
            if isinstance(new_state, dict):
                if not save_user_state(sender, new_state):
                    logger.error(f"Failed to save state for {sender}")
            else:
                logger.error("Invalid state returned from message_handler")
            
            # Send response back to WhatsApp
            # Uncomment to enable responses
            send(response, sender, phone_id)
            
            return jsonify({"status": "ok"}), 200

        except Exception as e:
            logger.error(f"Error processing webhook: {str(e)}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500

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
        "Authorization": f"Bearer {wa_token}",
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
        logger.info(f"Message sent to {recipient}")
        return True
    except Exception as e:
        logger.error(f"Error sending message to {recipient}: {str(e)}")
        return False

# ==================== Main Execution ====================
if __name__ == "__main__":
    if not check_redis_connection():
        logger.error("Failed to connect to Redis - check configuration")
    else:
        logger.info("Redis connection successful")
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
