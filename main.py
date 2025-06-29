import os
import json
import requests
import logging
from flask import Flask, request, jsonify, render_template
import google.generativeai as genai

app = Flask(__name__)

# Environment variables
WA_TOKEN = os.environ.get("WA_TOKEN")  # WhatsApp API Key
PHONE_ID = os.environ.get("PHONE_ID")
GEN_API_KEY = os.environ.get("GEN_API")  # Gemini API Key
OWNER_PHONE = os.environ.get("OWNER_PHONE")
GRAPH_API_BASE = "https://graph.facebook.com/v19.0"

# Configure logging
logger = logging.getLogger("main")
logging.basicConfig(level=logging.INFO)
logger.info(f"Initializing WhatsApp bot with PHONE_ID: {PHONE_ID}")

# Import Redis Upstash functions
from redis_utils import get_user_state, update_user_state

# ======================
# MESSAGE SENDING FUNCTIONS
# ======================

def send_text_message(recipient, message):
    """Send text message via WhatsApp API"""
    if not all([WA_TOKEN, PHONE_ID, recipient, message]):
        logger.error("Missing required parameters for sending message")
        return False

    url = f"{GRAPH_API_BASE}/{PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "text",
        "text": {"body": message}
    }

    try:
        logger.info(f"Attempting to send message to {recipient}")
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        logger.info(f"Message sent successfully to {recipient}")
        return True
    except requests.exceptions.HTTPError as err:
        logger.error(f"HTTP Error: {err.response.status_code} - {err.response.text}")
    except Exception as e:
        logger.error(f"Failed to send message: {str(e)}")
    return False

def send_list_message(recipient, text, options, title="Select an option"):
    """Send WhatsApp interactive list message"""
    url = f"{GRAPH_API_BASE}/{PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": title},
            "body": {"text": text},
            "action": {
                "button": "Options",
                "sections": [{
                    "title": "Choose one",
                    "rows": [
                        {"id": opt.lower().replace(" ", "_"), "title": opt} 
                        for opt in options
                    ]
                }]
            }
        }
    }
    
    try:
        logger.info(f"Sending list message to {recipient}")
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send list message: {str(e)}")
        return False

def send_button_message(recipient, text, buttons):
    """Send WhatsApp quick reply buttons"""
    url = f"{GRAPH_API_BASE}/{PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": text},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": btn.lower().replace(" ", "_"),
                            "title": btn
                        }
                    } for btn in buttons
                ]
            }
        }
    }
    
    try:
        logger.info(f"Sending button message to {recipient}")
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send button message: {str(e)}")
        return False

# ======================
# WEBHOOK HANDLERS
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

        # Initialize user state
        user_state = get_user_state(sender) or {
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
# MESSAGE TYPE HANDLERS
# ======================

def handle_image_message(message, sender, name, user_state):
    current_step = user_state.get("step", "start")
    if current_step == "awaiting_image" and not user_state.get("image_received", False):
        user_state.update({
            "image_received": True,
            "verified": True,
            "step": "manual"
        })
        update_user_state(sender, user_state)
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
        update_user_state(sender, user_state)
        send_list_message(
            sender,
            "Hello! Are you a student or landlord?",
            ["Student", "Landlord"],
            "User Type"
        )
    else:
        send_text_message(sender, "Please use the menu options provided.")

# ======================
# CONVERSATION HANDLERS
# ======================

def handle_start(selected_option, sender, name, user_state):
    if selected_option == "landlord":
        user_state.update({
            "step": "awaiting_image",
            "verified": False,
            "image_received": False
        })
        update_user_state(sender, user_state)
        send_text_message(
            sender,
            "Great! Please send a screenshot of your WhatsApp profile for verification."
        )
    elif selected_option == "student":
        user_state["step"] = "student_pending"
        update_user_state(sender, user_state)
        send_text_message(
            sender,
            "Welcome student! Please download our app to find accommodation."
        )

def handle_awaiting_image(selected_option, sender, name, user_state):
    """Handle the awaiting image verification step"""
    if selected_option == "image":
        user_state.update({
            "image_received": True,
            "verified": True,
            "step": "manual"
        })
        update_user_state(sender, user_state)
        send_list_message(
            sender,
            "Is your accommodation for boys, girls, or mixed?",
            ["Boys", "Girls", "Mixed"],
            "Accommodation Type"
        )
    else:
        send_text_message(
            sender,
            "Please send an image (screenshot of your WhatsApp profile) to verify your identity."
        )
        
def handle_manual_house_type(selected_option, sender, name, user_state):
    user_state["house_type"] = selected_option
    user_state["step"] = "ask_cat_owner"
    update_user_state(sender, user_state)
    send_button_message(
        sender,
        "Do you have a cat?",
        ["Yes", "No"]
    )

def handle_ask_cat_owner(selected_option, sender, name, user_state):
    user_state["has_cat"] = selected_option
    user_state["step"] = "ask_availability"
    update_user_state(sender, user_state)
    send_button_message(
        sender,
        "Do you have vacancies?",
        ["Yes", "No"]
    )


def handle_ask_single_room_count(selected_option, sender, name, user_state):
    """Handle single room count input"""
    if selected_option.isdigit():
        user_state["room_single"] = int(selected_option)
        user_state["step"] = "confirm_single"
        update_user_state(sender, user_state)
        send_text_message(sender, "What's the rent for single rooms? (Amount only)")
    else:
        send_text_message(sender, "Please enter a valid number for single rooms.")

def handle_confirm_single_rent(selected_option, sender, name, user_state):
    """Handle single room rent input"""
    try:
        rent = float(selected_option)
        user_state["rent_single"] = rent
        user_state["step"] = "ask_2_sharing"
        update_user_state(sender, user_state)
        send_text_message(sender, "How many need 2-sharing rooms? (Number only)")
    except ValueError:
        send_text_message(sender, "Please enter a valid rent amount.")


def handle_ask_availability(selected_option, sender, name, user_state):
    if selected_option == "no":
        user_state["step"] = "end"
        update_user_state(sender, user_state)
        send_text_message(
            sender,
            "OK thanks. Whenever you have vacancies, don't hesitate to say 'Hi!'"
        )
    else:
        user_state["step"] = "ask_room_type"
        update_user_state(sender, user_state)
        send_text_message(
            sender,
            "How many need single rooms? (Reply with number only)"
        )


def handle_ask_2_sharing_count(selected_option, sender, name, user_state):
    """Handle 2-sharing room count input"""
    if selected_option.isdigit():
        user_state["room_2_sharing"] = int(selected_option)
        user_state["step"] = "confirm_2_sharing"
        update_user_state(sender, user_state)
        send_text_message(sender, "What's the rent for 2-sharing rooms? (Amount only)")
    else:
        send_text_message(sender, "Please enter a valid number for 2-sharing rooms.")


def handle_confirm_2_sharing_rent(selected_option, sender, name, user_state):
    """Handle 2-sharing room rent input"""
    try:
        rent = float(selected_option)
        user_state["rent_2_sharing"] = rent
        user_state["step"] = "ask_3_sharing"
        update_user_state(sender, user_state)
        send_text_message(sender, "How many need 3-sharing rooms? (Number only)")
    except ValueError:
        send_text_message(sender, "Please enter a valid rent amount.")

def handle_ask_3_sharing_count(selected_option, sender, name, user_state):
    """Handle 3-sharing room count input"""
    if selected_option.isdigit():
        user_state["room_3_sharing"] = int(selected_option)
        user_state["step"] = "confirm_3_sharing"
        update_user_state(sender, user_state)
        send_text_message(sender, "What's the rent for 3-sharing rooms? (Amount only)")
    else:
        send_text_message(sender, "Please enter a valid number for 3-sharing rooms.")

def handle_confirm_3_sharing_rent(selected_option, sender, name, user_state):
    """Handle 3-sharing room rent input"""
    try:
        rent = float(selected_option)
        user_state["rent_3_sharing"] = rent
        user_state["step"] = "ask_student_age"
        update_user_state(sender, user_state)
        send_text_message(sender, "What age group are the students? (e.g. 18-22)")
    except ValueError:
        send_text_message(sender, "Please enter a valid rent amount.")

def handle_ask_student_age(selected_option, sender, name, user_state):
    """Handle student age group input"""
    user_state["student_age"] = selected_option
    user_state["step"] = "confirm_listing"
    update_user_state(sender, user_state)
    send_button_message(
        sender,
        "Please confirm your listing details",
        ["Confirm", "Cancel"]
    )

def handle_confirm_listing(selected_option, sender, name, user_state):
    """Handle listing confirmation"""
    if selected_option.lower() == "confirm":
        # Process the listing confirmation
        user_state["step"] = "end"
        update_user_state(sender, user_state)
        send_text_message(
            sender,
            "Thank you! Your listing has been submitted for approval."
        )
        # Optionally notify admin
        if OWNER_PHONE:
            send_text_message(
                OWNER_PHONE,
                f"New listing from {name} ({sender}):\n"
                f"House Type: {user_state.get('house_type')}\n"
                f"Single Rooms: {user_state.get('room_single')} @ {user_state.get('rent_single')}\n"
                f"2-Sharing: {user_state.get('room_2_sharing')} @ {user_state.get('rent_2_sharing')}\n"
                f"3-Sharing: {user_state.get('room_3_sharing')} @ {user_state.get('rent_3_sharing')}\n"
                f"Age Group: {user_state.get('student_age')}"
            )
    else:
        user_state["step"] = "end"
        update_user_state(sender, user_state)
        send_text_message(
            sender,
            "Listing cancelled. Type 'Hi' to start again."
        )

def handle_student_pending(selected_option, sender, name, user_state):
    """Handle student pending state"""
    send_text_message(
        sender,
        "Please download our app from [app link] to find accommodation."
    )


def handle_default(selected_option, sender, name, user_state):
    send_text_message(sender, "Sorry, I didn't understand that. Type 'Hi' to start over.")
    user_state["step"] = "start"
    update_user_state(sender, user_state)

# ======================
# UTILITY FUNCTIONS
# ======================

def get_current_prompt(step):
    prompts = {
        "start": "Please select an option from the menu",
        "awaiting_image": "Please send screenshot of your WhatsApp profile for verification",
        "manual": "Please select accommodation type from the menu",
        # ... other prompts ...
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
    "ask_room_type": handle_ask_single_room_count,
    "confirm_single": handle_confirm_single_rent,
    "ask_2_sharing": handle_ask_2_sharing_count,
    "confirm_2_sharing": handle_confirm_2_sharing_rent,
    "ask_3_sharing": handle_ask_3_sharing_count,
    "confirm_3_sharing": handle_confirm_3_sharing_rent,
    "ask_student_age": handle_ask_student_age,
    "confirm_listing": handle_confirm_listing,
    "student_pending": handle_student_pending,
    "end": handle_end,
}

if __name__ == "__main__":
    # Verify essential environment variables
    if not all([WA_TOKEN, PHONE_ID]):
        logger.error("Missing required environment variables!")
        exit(1)
        
    app.run(host="0.0.0.0", port=5000, debug=True)
