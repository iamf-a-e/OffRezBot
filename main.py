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
    """Handle incoming messages with proper validation"""
    # Validate inputs
    if not message or not isinstance(message, str):
        return "Please send a valid message", user_state or {}
    
    if not isinstance(user_state, dict):
        user_state = {}
    
    msg = message.strip().lower()
    landlord_name = user_state.get("landlord_name", "Landlord")
    student_name = user_state.get("student_name", "the student")

    # First time user onboarding
    if not user_state.get("user_type"):
        if msg in ["landlord", "student"]:
            user_state["user_type"] = msg
            if msg == "student":
                response = (
                    "Hi! If you are a student, please use the OffRez Student App to find and book accommodation. "
                    "This bot is for landlords only."
                )
                user_state["onboarded"] = True
                return response, user_state
            elif msg == "landlord":
                user_state["initiated_by"] = "landlord"
                user_state["onboarded"] = True
                response = (
                    "Welcome, landlord! 👋\n"
                    "Let's get you started with student housing registration.\n"
                    "Please provide your full name and the location of your property to begin the registration process."
                )
                return response, user_state
        else:
            response = (
                "Welcome! Are you a 'landlord' or a 'student'?\n"
                "Please reply with either 'landlord' or 'student'."
            )
            return response, user_state

    # Normal chat logic after onboarding
    initiated_by = user_state.get("initiated_by", "placement_team")
    semester = user_state.get("semester", "current")
    mass_messaging = user_state.get("mass_messaging", False)

    # Handle different conversation flows
    if initiated_by == "landlord":
        response = (f"Hello {landlord_name}, thank you for reaching out! 👋\n"
                   "How can we help you today regarding your student accommodation?\n"
                   "If you have vacancies or updates, please let us know below. 🏠")
        user_state["last_prompt"] = "awaiting_landlord_info"
        return response, user_state

    # [Rest of your message handling logic...]
    # ... (include all your existing message handling cases)

    # Fallback response
    return "Sorry, I didn't understand your response. Please try again.", user_state

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
            # send(response, sender, phone_id)
            
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
