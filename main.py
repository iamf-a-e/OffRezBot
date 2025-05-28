import os
import json
import requests
import logging
from flask import Flask, request, jsonify, render_template
import google.generativeai as genai
import base64

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

def advance(user_id, user_state, new_step, response=None):
   user_state["step"] = new_step
   save_user_state(user_id, user_state)
   return response, user_state
    


def message_handler(sender, message, user_state, value):
    user_id = user_state.get("user_id")  # Ensure user_id is present

    # Helper to check valid image extension
    def is_image_extension(fname):
        allowed_exts = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}
        return os.path.splitext(fname)[1].lower() in allowed_exts

    # --- Handle image message ---
    if isinstance(message, dict) and message.get("type") == "image":
        media_id = message["image"].get("id")
        if not media_id:
            send_message(sender, "Sorry, I couldn't get your image. Please try again.")
            return None, user_state

        # Step 1: Get media URL
        media_info_resp = requests.get(
            f"{GRAPH_API_BASE}/{media_id}",
            headers={"Authorization": f"Bearer {wa_token}"}
        )
        if media_info_resp.status_code != 200:
            send_message(sender, "Failed to get your image. Please try again.")
            return None, user_state

        media_url = media_info_resp.json().get("url")
        if not media_url:
            send_message(sender, "Failed to get your image URL. Please try again.")
            return None, user_state

        # Step 2: Download image content
        image_resp = requests.get(media_url, headers={"Authorization": f"Bearer {wa_token}"})
        if image_resp.status_code != 200:
            send_message(sender, "Failed to download your image. Please try again.")
            return None, user_state

        # Step 3: Detect file extension from Content-Type header
        content_type = image_resp.headers.get("Content-Type", "")
        mime_to_ext = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/bmp": ".bmp",
            "image/tiff": ".tiff",
            "image/webp": ".webp",
        }
        extension = mime_to_ext.get(content_type.lower(), ".jpg")  # default to .jpg

        # Step 4: Generate filename
        filename = f"{media_id}{extension}"

        if not is_image_extension(filename):
            send_message(sender, "Please upload a valid image file (jpg, png, gif, etc).")
            return None, user_state

        # Step 5: Convert image content to base64 and save
        image_base64 = base64.b64encode(image_resp.content).decode("utf-8")
        user_state["image_base64"] = image_base64
        user_state["image_filename"] = filename
        save_user_state(sender, user_state)

        # Step 6: Send approval prompt
        approval_msg = advance(
            sender,
            user_state,
            "approve_manual",
            "Thanks! Approval will be done manually for security reasons.\n\n"
            "Now let’s collect house details.\n\nDo you have accommodation for *boys*, *girls*, or *mixed*?"
        )
        send(approval_msg, sender, value.get("metadata", {}).get("phone_number_id"))
        return None, user_state

    # --- Handle text messages ---
    if isinstance(message, str):
        msg = message.strip().lower()
    else:
        msg = ""

    step = user_state.get("step", "start")

    # Detect if returning user wants to restart
    if msg == "hi" and user_state.get("step") == "end":
        user_state["step"] = "returning_user_menu"
        save_user_state(user_id, user_state)
        return (
            "Welcome back! What would you like to do today?\n"
            "1. Post a new vacancy\n"
            "2. Update existing listing\n"
            "3. Contact placement team"
        ), user_state

    # Handling awaiting landlord info
    if user_state.get("last_prompt") == "awaiting_landlord_info":
        if "yes" in msg:
            return advance(sender, user_state, "end", "Great! Expect the student's call/message soon. 📞")
        elif "no" in msg:
            return advance(sender, user_state, "end", "Ok thanks")
        elif "fully occupied" in msg:
            return advance(sender, user_state, "end", "Ok, whenever you need students just type the word 'Hie' 👋")

    # Step 0: Introduction
    if step == "start":
        if "student" in msg:
            return advance(sender, user_state, "student_redirect", 
                           "Please use the student app to check student hostel availability: https://playstore.com/xyz")
        if "landlord" in msg:
            return advance(sender, user_state, "get_whatsapp_verification", 
                           "OK. Please send a screenshot of your WhatsApp username with contact name back for verification.")
        else:
            return advance(sender, user_state, "start", 
                           "Hello, I’m the OffRez accommodation assistant. Are you a *student* or a *landlord*?")

    # Step 1: Identify user type
    if step == "ask_user_type":
        if msg == "landlord":
            return advance(sender, user_state, "get_whatsapp_verification", 
                           "OK. Please send a screenshot of your WhatsApp username with contact name back for verification.")
        if msg == "student":
            return advance(sender, user_state, "student_redirect", 
                           "Please use the student app to check student hostel availability: https://playstore.com/xyz")
        else:
            return advance(sender, user_state, "ask_user_type", 
                           "Please reply with either *student* or *landlord*.")

    # Step 3: Gender type
    if step == "approve_manual":
        if msg in ["boys", "girls", "mixed"]:
            user_state["house_type"] = msg
            return advance(sender, user_state, "ask_cat_owner", "Do you have a *cat*?")
        else:
            return advance(sender, user_state, "approve_manual", "Please answer with *boys*, *girls*, or *mixed*.")

    # Step 4: Ask about cat
    if step == "ask_cat_owner":
        if msg in ["yes", "no"]:
            user_state["has_cat"] = msg
            return advance(sender, user_state, "ask_availability", "Do you have a vacancy?")
        else:
            return advance(sender, user_state, "ask_cat_owner", "Do you have a *cat*? Please reply *yes* or *no*.")

    # Step 5: Availability
    if step == "ask_availability":
        if msg == "no":
            return advance(sender, user_state, "end", "OK thanks. Whenever you have vacancies, don’t hesitate to say 'Hi!'")
        if msg == "yes":
            return advance(sender, user_state, "ask_room_type", 
                           "How many *boys* or *girls* do you need accommodation for in *single rooms*? (reply with number only)")
        else:
            return advance(sender, user_state, "ask_availability", "Do you have a vacancy? Please reply *yes* or *no*.")

    # Step 6: Capture room info recursively
    if step.startswith("ask_room_type"):
        if msg.isdigit():
            user_state["room_count"] = int(msg)
            return advance(sender, user_state, "confirm_single", "Confirm your rent for single room (e.g. 1 is $130):")
        else:
            return advance(sender, user_state, "ask_room_type", "Please enter a number for how many students need single rooms.")

    if step == "confirm_single":
        if msg.replace(".", "").isdigit():
            user_state["rent_single"] = float(msg)
            return advance(sender, user_state, "ask_2_sharing", "How many students need 2-sharing rooms?")
        else:
            return advance(sender, user_state, "confirm_single", "Please reply with rent in numbers only (e.g. 130).")

    if step == "ask_2_sharing":
        if msg.isdigit():
            user_state["2_sharing"] = int(msg)
            return advance(sender, user_state, "confirm_2_sharing", "Confirm your rent for 2-sharing (e.g. 2 is $80):")
        else:
            return advance(sender, user_state, "ask_2_sharing", "Please enter number of students for 2-sharing.")

    if step == "confirm_2_sharing":
        if msg.replace(".", "").isdigit():
            user_state["rent_2_sharing"] = float(msg)
            return advance(sender, user_state, "ask_3_sharing", "How many students need 3-sharing rooms?")
        else:
            return advance(sender, user_state, "confirm_2_sharing", "Please reply with rent in numbers only (e.g. 80).")

    if step == "ask_3_sharing":
        if msg.isdigit():
            user_state["3_sharing"] = int(msg)
            return advance(sender, user_state, "confirm_3_sharing", "Confirm your rent for 3-sharing (e.g. 3 is $60):")
        else:
            return advance(sender, user_state, "ask_3_sharing", "Please enter number of students for 3-sharing.")

    if step == "confirm_3_sharing":
        if msg.replace(".", "").isdigit():
            user_state["rent_3_sharing"] = float(msg)
            return advance(sender, user_state, "ask_4_sharing", "How many students need 4-sharing rooms?")
        else:
            return advance(sender, user_state, "confirm_3_sharing", "Please reply with rent in numbers only (e.g. 60).")

    if step == "ask_4_sharing":
        if msg.isdigit():
            user_state["4_sharing"] = int(msg)
            return advance(sender, user_state, "confirm_4_sharing", "Confirm your rent for 4-sharing (e.g. 4 is $70):")
        else:
            return advance(sender, user_state, "ask_4_sharing", "Please enter number of students for 4-sharing.")

    if step == "confirm_4_sharing":
        if msg.replace(".", "").isdigit():
            user_state["rent_4_sharing"] = float(msg)
            return advance(sender, user_state, "end", 
                           "Thank you. I'm not a real machine, will get back to you. Whenever you need more students, just type 'Hi'!")
        else:
            return advance(sender, user_state, "confirm_4_sharing", "Please reply with rent in numbers only (e.g. 70).")

    # Returning user menu
    if step == "returning_user_menu":
        if msg in ["1", "post a new vacancy"]:
            return advance(sender, user_state, "get_whatsapp_verification", "Send a screenshot of your WhatsApp username with contact name.")
        elif msg in ["2", "update existing listing"]:
            return advance(sender, user_state, "ask_landlord", "Send your Whatsapp username to update your listing.")
        elif msg in ["3", "contact placement team"]:
            return advance(sender, user_state, "end", "Call or WhatsApp +263778099830 for the placement team.")
        else:
            return advance(sender, user_state, "returning_user_menu", "Please select 1, 2, or 3.")

    # Fallback catch-all
    return "I didn’t get that. Please try again or type 'Hi' to start over.", user_state


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

            # Handle image messages
            if message.get("type") == "image" and "image" in message:
                media_id = message["image"].get("id")
                if not media_id:
                    logger.error("Image ID missing")
                    return jsonify({"status": "error", "message": "Missing image ID"}), 400

                media_info_resp = requests.get(
                    f"{GRAPH_API_BASE}/{media_id}",
                    headers={"Authorization": f"Bearer {wa_token}"}
                )

                if media_info_resp.status_code != 200:
                    logger.error(f"Failed to get media URL: {media_info_resp.text}")
                    return jsonify({"status": "error", "message": "Failed to get media URL"}), 400

                media_url = media_info_resp.json().get("url")
                if not media_url:
                    logger.error("Media URL not found in response")
                    return jsonify({"status": "error", "message": "No media URL"}), 400

                image_resp = requests.get(media_url, headers={"Authorization": f"Bearer {wa_token}"})
                if image_resp.status_code != 200:
                    logger.error(f"Failed to download image: {image_resp.text}")
                    return jsonify({"status": "error", "message": "Failed to download image"}), 400

                image_base64 = base64.b64encode(image_resp.content).decode("utf-8")
                user_state["image_url"] = image_base64
                save_user_state(sender, user_state)

                approval_msg = advance(
                    sender,
                    user_state,
                    "approve_manual",
                    f"Thanks {name or 'there'}. Approval will be done manually for security reasons.\n\nNow let’s collect house details.\n\nDo you have accommodation for *boys*, *girls*, or *mixed*?"
                )
                send(approval_msg, sender, value.get("metadata", {}).get("phone_number_id"))
                return jsonify({"reply": approval_msg}), 200

            # Handle text messages
            if message.get("type") == "text" and "text" in message:
                text = message["text"]["body"].strip()
                logger.info(f"Processing text message from {sender}: '{text}'")
                reply, updated_state = message_handler(sender, text, user_state)
                save_user_state(sender, updated_state)
                send(reply, sender, value.get("metadata", {}).get("phone_number_id"))
                return jsonify({"reply": reply}), 200

            # Unhandled message types
            logger.info("Received unsupported message type")
            return jsonify({"status": "ignored", "message": "Unsupported message type"}), 200

        except Exception as e:
            logger.exception("Error handling webhook")
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


# ==================== Main Execution ====================
if __name__ == "__main__":
    if not check_redis_connection():
        logger.error("Failed to connect to Redis - check configuration")
    else:
        logger.info("Redis connection successful")
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
