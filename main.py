import os
import json
import requests
import logging
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Environment variables
WA_TOKEN = os.environ.get("WA_TOKEN")  # WhatsApp API Key
PHONE_ID = os.environ.get("PHONE_ID")  # WhatsApp Phone Number ID
OWNER_PHONE = os.environ.get("OWNER_PHONE")  # Admin phone number for notifications
GRAPH_API_BASE = "https://graph.facebook.com/v19.0"

# Configure logging
logger = logging.getLogger("main")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger.info(f"Initializing WhatsApp bot with PHONE_ID: {PHONE_ID}")

# ======================
# IMPROVED MESSAGE SENDING FUNCTIONS
# ======================

def _validate_whatsapp_config():
    """Validate required WhatsApp configuration"""
    if not all([WA_TOKEN, PHONE_ID]):
        logger.error("Missing WhatsApp API configuration (WA_TOKEN or PHONE_ID)")
        return False
    return True

def _send_whatsapp_request(recipient, payload):
    """Helper function to send WhatsApp API requests"""
    if not _validate_whatsapp_config():
        return False
        
    if not recipient:
        logger.error("No recipient specified")
        return False

    url = f"{GRAPH_API_BASE}/{PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json"
    }
    
    try:
        logger.info(f"Attempting to send message to {recipient}")
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        logger.info(f"Message sent successfully to {recipient}")
        return True
    except requests.exceptions.HTTPError as err:
        error_msg = f"HTTP Error: {err.response.status_code} - {err.response.text}"
        logger.error(error_msg)
        
        # Specific handling for 401 Unauthorized
        if err.response.status_code == 401:
            logger.error("Authentication failed - please check your WA_TOKEN and PHONE_ID")
    except Exception as e:
        logger.error(f"Failed to send message: {str(e)}")
    return False

def send_text_message(recipient, message):
    """Send text message via WhatsApp API"""
    if not message or len(message) > 4096:  # WhatsApp text message limit
        logger.error("Message is empty or too long")
        return False
        
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "text",
        "text": {"body": message}
    }
    return _send_whatsapp_request(recipient, payload)

def send_list_message(recipient, text, options, title="Select an option"):
    """Send WhatsApp interactive list message"""
    if len(options) > 10:
        logger.error("Too many options (max 10 allowed)")
        return False
        
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": title[:60]},  # Title character limit
            "body": {"text": text[:1024]},  # Body character limit
            "action": {
                "button": "Options",
                "sections": [{
                    "title": "Choose one",
                    "rows": [
                        {"id": opt.lower().replace(" ", "_"), "title": opt[:24]}  # Option character limit
                        for opt in options[:10]  # Max 10 options
                    ]
                }]
            }
        }
    }
    return _send_whatsapp_request(recipient, payload)

def send_button_message(recipient, text, buttons):
    """Send WhatsApp quick reply buttons"""
    if len(buttons) > 3:
        logger.error("Too many buttons (max 3 allowed)")
        return False
        
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": text[:1024]},  # Body character limit
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": btn.lower().replace(" ", "_"),
                            "title": btn[:20]  # Button title limit
                        }
                    } for btn in buttons[:3]  # Max 3 buttons
                ]
            }
        }
    }
    return _send_whatsapp_request(recipient, payload)

# ======================
# WEBHOOK HANDLERS (unchanged from your original)
# ======================

@app.route("/", methods=["GET"])
def index():
    return render_template("connected.html")

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    logger.info(f"Webhook verification attempt - Mode: {mode}, Token: {token}")

    if mode == "subscribe" and token == "BOT":
        logger.info("Webhook verified successfully")
        return challenge, 200
    logger.warning("Webhook verification failed")
    return "Verification failed", 403

@app.route("/webhook", methods=["POST"])
def handle_webhook():
    try:
        data = request.get_json()
        logger.info(f"Incoming webhook data: {json.dumps(data, indent=2)}")

        # Validate incoming data structure
        if not data.get("entry"):
            logger.error("No entries in webhook data")
            return jsonify({"status": "error", "message": "Invalid data format"}), 400

        entry = data["entry"][0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])

        if not messages:
            logger.info("No messages in webhook payload")
            return jsonify({"status": "ok", "message": "No messages"}), 200

        message = messages[0]
        sender = message.get("from")
        if not sender:
            logger.error("No sender in message")
            return jsonify({"status": "error", "message": "No sender"}), 400

        # Extract contact info
        contact = value.get("contacts", [{}])[0]
        name = contact.get("profile", {}).get("name", "there")
        msg_type = message.get("type")

        # Initialize user state (simplified for example)
        user_state = {
            "user": {"name": name},
            "user_id": sender,
            "step": "start",
            "verified": False,
            "image_received": False
        }
        current_step = user_state.get("step", "start")

        # Handle interactive responses
        interactive_response = None
        if msg_type == "interactive":
            interactive_type = message["interactive"]["type"]
            if interactive_type == "list_reply":
                interactive_response = message["interactive"]["list_reply"]["id"]
            elif interactive_type == "button_reply":
                interactive_response = message["interactive"]["button_reply"]["id"]
            msg_type = "interactive"

        # Process message based on type
        if msg_type == "image":
            handle_image_message(message, sender, name, user_state)
        elif msg_type == "interactive":
            handle_interactive_message(interactive_response, sender, name, user_state, current_step)
        elif msg_type == "text":
            handle_text_message(message, sender, name, user_state, current_step)
        else:
            send_text_message(sender, get_current_prompt(current_step))

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.exception(f"Error processing webhook: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ======================
# MESSAGE TYPE HANDLERS (unchanged from your original)
# ======================

def handle_image_message(message, sender, name, user_state):
    current_step = user_state.get("step", "start")
    if current_step == "awaiting_image" and not user_state.get("image_received", False):
        user_state.update({
            "image_received": True,
            "verified": True,
            "step": "manual"
        })
        send_list_message(
            sender,
            "Is your accommodation for boys, girls, or mixed?",
            ["Boys", "Girls", "Mixed"],
            "Accommodation Type"
        )
    else:
        send_text_message(sender, get_current_prompt(current_step))

def handle_interactive_message(selected_option, sender, name, user_state, current_step):
    handler = ACTION_MAPPING.get(current_step, handle_default)
    handler(selected_option, sender, name, user_state)

def handle_text_message(message, sender, name, user_state, current_step):
    msg = message.get("text", {}).get("body", "").strip().lower()
    if msg in ["hi", "hello", "hey"]:
        user_state["step"] = "start"
        send_list_message(
            sender,
            "Hello! Are you a student or landlord?",
            ["Student", "Landlord"],
            "User Type"
        )
    else:
        send_text_message(sender, "Please use the menu options provided.")

# ======================
# CONVERSATION HANDLERS (unchanged from your original)
# ======================

def handle_start(selected_option, sender, name, user_state):
    if selected_option == "landlord":
        user_state.update({
            "step": "awaiting_image",
            "verified": False,
            "image_received": False
        })
        send_text_message(
            sender,
            "Great! Please send a screenshot of your WhatsApp profile for verification."
        )
    elif selected_option == "student":
        user_state["step"] = "student_pending"
        send_text_message(
            sender,
            "Welcome student! Please download our app to find accommodation."
        )

def handle_awaiting_image(selected_option, sender, name, user_state):
    """Handle the awaiting image verification step"""
    send_text_message(
        sender,
        "Please send an image (screenshot of your WhatsApp profile) to verify your identity."
    )
        
def handle_manual_house_type(selected_option, sender, name, user_state):
    user_state["house_type"] = selected_option
    user_state["step"] = "ask_cat_owner"
    send_button_message(
        sender,
        "Do you have a cat?",
        ["Yes", "No"]
    )

def handle_ask_cat_owner(selected_option, sender, name, user_state):
    user_state["has_cat"] = selected_option
    user_state["step"] = "ask_availability"
    send_button_message(
        sender,
        "Do you have vacancies?",
        ["Yes", "No"]
    )

def handle_ask_availability(selected_option, sender, name, user_state):
    if selected_option == "no":
        user_state["step"] = "end"
        send_text_message(
            sender,
            "OK thanks. Whenever you have vacancies, don't hesitate to say 'Hi!'"
        )
    else:
        user_state["step"] = "ask_room_type"
        send_text_message(
            sender,
            "How many need single rooms? (Reply with number only)"
        )

def handle_default(selected_option, sender, name, user_state):
    send_text_message(sender, "Sorry, I didn't understand that. Type 'Hi' to start over.")
    user_state["step"] = "start"

# ======================
# UTILITY FUNCTIONS
# ======================

def get_current_prompt(step):
    prompts = {
        "start": "Please select an option from the menu",
        "awaiting_image": "Please send screenshot of your WhatsApp profile for verification",
        "manual": "Please select accommodation type from the menu",
        "ask_cat_owner": "Do you have a cat?",
        "ask_availability": "Do you have vacancies?",
        "ask_room_type": "How many single rooms are available?",
        "end": "Thank you for using our service. Type 'Hi' to start again."
    }
    return prompts.get(step, "Please select an option to continue.")

# ======================
# ACTION MAPPING
# ======================

ACTION_MAPPING = {
    "start": handle_start,
    "awaiting_image": handle_awaiting_image,
    "manual": handle_manual_house_type,
    "ask_cat_owner": handle_ask_cat_owner,
    "ask_availability": handle_ask_availability,
    "ask_room_type": handle_ask_availability,
    "end": handle_default
}

if __name__ == "__main__":
    # Verify essential environment variables
    if not all([WA_TOKEN, PHONE_ID]):
        logger.error("Missing required environment variables!")
        logger.error("Please set WA_TOKEN and PHONE_ID in your environment")
        exit(1)
        
    # Test WhatsApp connection
    if OWNER_PHONE:
        test_msg = "WhatsApp Bot started successfully!"
        if send_text_message(OWNER_PHONE, test_msg):
            logger.info("Test message sent successfully to admin")
        else:
            logger.error("Failed to send test message to admin")
    
    app.run(host="0.0.0.0", port=5000, debug=True)
