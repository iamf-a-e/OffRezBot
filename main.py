import os
import json
import requests
import logging
from flask import Flask, request, jsonify, render_template
import google.generativeai as genai

app = Flask(__name__)

# Environment variables
wa_token = os.environ.get("WA_TOKEN")  # WhatsApp API Key
WA_TOKEN = wa_token
phone_id = os.environ.get("PHONE_ID")
genai.configure(api_key=os.environ.get("GEN_API"))    # Gemini API Key
owner_phone = os.environ.get("OWNER_PHONE")
GRAPH_API_BASE = "https://graph.facebook.com/v19.0"

logger = logging.getLogger("main")
logging.basicConfig(level=logging.INFO)

# Import Redis Upstash functions
from redis_utils import get_user_state, update_user_state, save_user_state

def send_text_message(recipient, phone_id, message):
    """Send text message via WhatsApp API"""
    if not all([message, recipient, phone_id]):
        logger.error("Missing parameters in send()")
        return False

    url = f"{GRAPH_API_BASE}/{phone_id}/messages"
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
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        logger.info(f"Sent message to {recipient}: {message}")
        return True
    except Exception as e:
        logger.error(f"Failed to send message to {recipient}: {e}")
        return False

def send_list_message(recipient, phone_id, text, options, title="Select an option"):
    """Send WhatsApp interactive list message"""
    url = f"{GRAPH_API_BASE}/{phone_id}/messages"
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
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send list message: {e}")
        return False

def send_button_message(recipient, phone_id, text, buttons):
    """Send WhatsApp quick reply buttons"""
    url = f"{GRAPH_API_BASE}/{phone_id}/messages"
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
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send button message: {e}")
        return False

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
            return challenge, 200
        return "Verification failed", 403

    if request.method == "POST":
        data = request.get_json()
        logger.info(f"Incoming webhook data: {json.dumps(data, indent=2)}")

        try:
            entry = data.get("entry", [])[0]
            changes = entry.get("changes", [])[0]
            value = changes.get("value", {})
            messages = value.get("messages", [])

            if not messages:
                return jsonify({"status": "ok", "message": "No messages"}), 200

            message = messages[0]
            sender = message.get("from")
            if not sender:
                return jsonify({"status": "error", "message": "No sender"}), 400

            name = value.get("contacts", [{}])[0].get("profile", {}).get("name", "")
            msg_type = message.get("type")
            
            # Get interactive response if exists
            interactive_response = None
            if msg_type == "interactive":
                interactive_type = message["interactive"]["type"]
                if interactive_type == "list_reply":
                    interactive_response = message["interactive"]["list_reply"]["id"]
                elif interactive_type == "button_reply":
                    interactive_response = message["interactive"]["button_reply"]["id"]
                msg_type = "interactive"

            # Initialize user state
            user_state = get_user_state(sender) or {
                "user": {"name": name},
                "user_id": sender,
                "step": "start",
                "verified": False,
                "image_received": False
            }
            current_step = user_state.get("step", "start")

            # Handle image messages
            if msg_type == "image":
                if current_step == "awaiting_image" and not user_state.get("image_received", False):
                    user_state["image_received"] = True
                    user_state["verified"] = True
                    user_state["step"] = "manual"
                    update_user_state(sender, user_state)
                    send_list_message(
                        sender, phone_id,
                        "Is your accommodation for boys, girls, or mixed?",
                        ["Boys", "Girls", "Mixed"],
                        "Accommodation Type"
                    )
                    return jsonify({"status": "ok"}), 200
                else:
                    send_text_message(sender, phone_id, get_current_prompt(current_step))
                    return jsonify({"status": "ok"}), 200

            # Handle interactive messages (list/button responses)
            if msg_type == "interactive":
                handler = action_mapping.get(current_step, handle_default)
                return handler(interactive_response, sender, name, user_state, phone_id, msg_type)

            # Handle text messages (only for special cases like 'hi')
            if msg_type == "text":
                msg = message.get("text", {}).get("body", "").strip().lower()
                if msg in ["hi", "hello", "hey"]:
                    if current_step != "start":
                        user_state["step"] = "start"
                        update_user_state(sender, user_state)
                    send_list_message(
                        sender, phone_id,
                        "Hello! Are you a student or landlord?",
                        ["Student", "Landlord"],
                        "User Type"
                    )
                    return jsonify({"status": "ok"}), 200
                else:
                    # Fallback for text input when not expected
                    send_text_message(sender, phone_id, "Please use the menu options provided.")
                    return jsonify({"status": "ok"}), 200

            # Handle other message types
            send_text_message(sender, phone_id, get_current_prompt(current_step))
            return jsonify({"status": "ok"}), 200

        except Exception as e:
            logger.exception(f"Error processing message: {str(e)}")
            return jsonify({"status": "error", "message": str(e)}), 500

def get_current_prompt(step):
    """Returns the appropriate prompt for the current step"""
    prompts = {
        "start": "Please select an option from the menu",
        "awaiting_image": "Please send screenshot of your WhatsApp profile for verification",
        "manual": "Please select accommodation type from the menu",
        "ask_cat_owner": "Do you have a cat?",
        "ask_availability": "Do you have vacancies?",
        "ask_room_type": "How many need single rooms? (number only)",
        "confirm_single": "What's the rent for single rooms? (amount only)",
        "ask_2_sharing": "How many need 2-sharing rooms? (number only)",
        "confirm_2_sharing": "What's the rent for 2-sharing rooms? (amount only)",
        "ask_3_sharing": "How many need 3-sharing rooms? (number only)",
        "confirm_3_sharing": "What's the rent for 3-sharing rooms? (amount only)",
        "ask_student_age": "What age group? (e.g. 18-22)",
        "confirm_listing": "Please confirm your listing",
        "student_pending": "Please download our app from [link]",
        "end": "Type 'Hi' to start again"
    }
    return prompts.get(step, "Please select an option to continue.")

def handle_start(selected_option, sender, name, user_state, phone_id, msg_type=None):
    if selected_option == "landlord":
        user_state.update({
            "step": "awaiting_image",
            "verified": False,
            "image_received": False
        })
        update_user_state(sender, user_state)
        send_text_message(
            sender, phone_id,
            "Great! Please send a screenshot of your WhatsApp profile for verification."
        )
    elif selected_option == "student":
        user_state["step"] = "student_pending"
        update_user_state(sender, user_state)
        send_text_message(
            sender, phone_id,
            "Welcome student! Please download our app to find accommodation."
        )
    else:
        send_list_message(
            sender, phone_id,
            "Please select if you're a student or landlord",
            ["Student", "Landlord"],
            "User Type"
        )
    return jsonify({"status": "ok"})

def handle_manual_house_type(selected_option, sender, name, user_state, phone_id, msg_type=None):
    user_state["house_type"] = selected_option
    user_state["step"] = "ask_cat_owner"
    update_user_state(sender, user_state)
    send_button_message(
        sender, phone_id,
        "Do you have a cat?",
        ["Yes", "No"]
    )
    return jsonify({"status": "ok"})

def handle_ask_cat_owner(selected_option, sender, name, user_state, phone_id, msg_type=None):
    user_state["has_cat"] = selected_option
    user_state["step"] = "ask_availability"
    update_user_state(sender, user_state)
    send_button_message(
        sender, phone_id,
        "Do you have vacancies?",
        ["Yes", "No"]
    )
    return jsonify({"status": "ok"})

def handle_ask_availability(selected_option, sender, name, user_state, phone_id, msg_type=None):
    if selected_option == "no":
        user_state["step"] = "end"
        update_user_state(sender, user_state)
        send_text_message(
            sender, phone_id,
            "OK thanks. Whenever you have vacancies, don't hesitate to say 'Hi!'"
        )
    else:
        user_state["step"] = "ask_room_type"
        update_user_state(sender, user_state)
        send_text_message(
            sender, phone_id,
            "How many need single rooms? (Reply with number only)"
        )
    return jsonify({"status": "ok"})

def handle_start(msg, sender, name, user_state, phone_id):
    msg = msg.lower()
    
    if msg in ["hi", "hello", "hey"]:
        reply = "Hello! Are you a *student* or *landlord*?"
        user_state["step"] = "start"
    elif msg == "landlord":
        reply = "Great! Please send a screenshot of your WhatsApp profile for verification."
        user_state.update({
            "step": "awaiting_image",
            "verified": False,
            "image_received": False
        })
    elif msg == "student":
        reply = "Welcome student! Please download our app to find accommodation."
        user_state["step"] = "student_pending"
    else:
        reply = "Please specify if you're a *student* or *landlord*."
    
    update_user_state(sender, user_state)
    send(reply, sender, phone_id)
    return jsonify({"status": "ok"})

def handle_awaiting_image(msg, sender, name, user_state, phone_id, msg_type):
    if msg_type == "image":
        reply = (
            f"Thanks {name or 'there'} for the image.\n\n"
            "Now let's collect house details.\n\n"
            "Do you have accommodation for *boys*, *girls*, or *mixed*?"
        )
        user_state["step"] = "manual"
    else:
        reply = "Please send an image (a screenshot of your WhatsApp profile name with your contact name) to proceed."

    update_user_state(sender, user_state)
    send(reply, sender, phone_id)
    return jsonify({"status": "ok"})

def handle_manual_house_type(msg, sender, name, user_state, phone_id, msg_type=None):
    msg = msg.lower()
    if msg in ["boys", "girls", "mixed"]:
        user_state["house_type"] = msg
        reply = "Do you have a *cat*? Please reply *yes* or *no*."
        user_state["step"] = "ask_cat_owner"
    else:
        reply = "Please reply with *boys*, *girls*, or *mixed*."

    update_user_state(sender, user_state)
    send(reply, sender, phone_id)
    return jsonify({"status": "ok"})

def handle_ask_cat_owner(msg, sender, name, user_state, phone_id, msg_type=None):
    msg = msg.lower()
    if msg in ["yes", "no"]:
        user_state["has_cat"] = msg
        reply = "Do you have a vacancy? Reply *yes* or *no*."
        user_state["step"] = "ask_availability"
    else:
        reply = "Do you have a cat? Please reply *yes* or *no*."

    update_user_state(sender, user_state)
    send(reply, sender, phone_id)
    return jsonify({"status": "ok"})

def handle_ask_availability(msg, sender, name, user_state, phone_id, msg_type=None):
    msg = msg.lower()
    if msg == "no":
        reply = "OK thanks. Whenever you have vacancies, don't hesitate to say 'Hi!'"
        user_state["step"] = "end"
    elif msg == "yes":
        reply = "How many *boys* or *girls* do you need accommodation for in *single rooms*? (Enter number only)"
        user_state["step"] = "ask_room_type"
    else:
        reply = "Do you have a vacancy? Please reply *yes* or *no*."

    update_user_state(sender, user_state)
    send(reply, sender, phone_id)
    return jsonify({"status": "ok"})

def handle_ask_single_room_count(msg, sender, name, user_state, phone_id, msg_type=None):
    if msg.isdigit():
        user_state["room_single"] = int(msg)
        reply = "Please confirm your rent for a single room (e.g. 130)."
        user_state["step"] = "confirm_single"
    else:
        reply = "Please enter the number of students needing single rooms (number only)."

    update_user_state(sender, user_state)
    send(reply, sender, phone_id)
    return jsonify({"status": "ok"})

def handle_confirm_single_rent(msg, sender, name, user_state, phone_id, msg_type=None):
    try:
        rent = float(msg)
        user_state["rent_single"] = rent
        reply = "How many students need 2-sharing rooms? (Enter number only)"
        user_state["step"] = "ask_2_sharing"
    except ValueError:
        reply = "Please enter the rent as a number (e.g. 130)."

    update_user_state(sender, user_state)
    send(reply, sender, phone_id)
    return jsonify({"status": "ok"})

def handle_ask_2_sharing_count(msg, sender, name, user_state, phone_id, msg_type=None):
    if msg.isdigit():
        user_state["room_2_sharing"] = int(msg)
        reply = "Please confirm your rent for 2-sharing rooms (e.g. 80)."
        user_state["step"] = "confirm_2_sharing"
    else:
        reply = "Please enter number of students needing 2-sharing rooms (number only)."

    update_user_state(sender, user_state)
    send(reply, sender, phone_id)
    return jsonify({"status": "ok"})

def handle_confirm_2_sharing_rent(msg, sender, name, user_state, phone_id, msg_type=None):
    try:
        rent = float(msg)
        user_state["rent_2_sharing"] = rent
        reply = "How many students need 3-sharing rooms? (Enter number only)"
        user_state["step"] = "ask_3_sharing"
    except ValueError:
        reply = "Please enter the rent as a number (e.g. 80)."

    update_user_state(sender, user_state)
    send(reply, sender, phone_id)
    return jsonify({"status": "ok"})

def handle_ask_3_sharing_count(msg, sender, name, user_state, phone_id, msg_type=None):
    if msg.isdigit():
        user_state["room_3_sharing"] = int(msg)
        reply = "Please confirm your rent for 3-sharing rooms (e.g. 60)."
        user_state["step"] = "confirm_3_sharing"
    else:
        reply = "Please enter number of students needing 3-sharing rooms (number only)."

    update_user_state(sender, user_state)
    send(reply, sender, phone_id)
    return jsonify({"status": "ok"})

def handle_confirm_3_sharing_rent(msg, sender, name, user_state, phone_id, msg_type=None):
    try:
        rent = float(msg)
        user_state["rent_3_sharing"] = rent
        reply = "What age group are the students? (e.g. 18-22)"
        user_state["step"] = "ask_student_age"
    except ValueError:
        reply = "Please enter the rent as a number (e.g. 60)."

    update_user_state(sender, user_state)
    send(reply, sender, phone_id)
    return jsonify({"status": "ok"})

def handle_ask_student_age(msg, sender, name, user_state, phone_id, msg_type=None):
    user_state["student_age"] = msg
    reply = "Thank you. Please confirm your listing by typing *confirm* or type *cancel* to abort."
    user_state["step"] = "confirm_listing"

    update_user_state(sender, user_state)
    send(reply, sender, phone_id)
    return jsonify({"status": "ok"})

def handle_confirm_listing(msg, sender, name, user_state, phone_id, msg_type=None):
    msg = msg.lower()
    if msg == "confirm":
        reply = "Thank you! Your listing will be published soon."
        user_state["step"] = "end"
    elif msg == "cancel":
        reply = "Your listing was cancelled. Type 'Hi' to start over."
        user_state["step"] = "end"
    else:
        reply = "Please type *confirm* to publish your listing or *cancel* to abort."

    update_user_state(sender, user_state)
    send(reply, sender, phone_id)
    return jsonify({"status": "ok"})


def handle_student_pending(msg, sender, name, user_state, phone_id, msg_type=None):
    reply = "Please download our app to secure your accommodation."
    update_user_state(sender, user_state)
    send(reply, sender, phone_id)
    return jsonify({"status": "ok"})

def handle_end(msg, sender, name, user_state, phone_id, msg_type=None):
    msg = msg.lower()
    if msg in ["hi", "hie", "hey"]:
        reply = "Welcome back! Are you a *student* or a *landlord*?"
        user_state["step"] = "start"
    else:
        reply = "Thank you for contacting us. Type 'Hi' to start again."

    update_user_state(sender, user_state)
    send(reply, sender, phone_id)
    return jsonify({"status": "ok"})

def handle_default(msg, sender, name, user_state, phone_id, msg_type=None):
    reply = "Sorry, I didn't understand that. Type 'Hi' to start over."
    user_state["step"] = "start"
    update_user_state(sender, user_state)
    send(reply, sender, phone_id)
    return jsonify({"status": "ok"})

def handle_confirm_listing(selected_option, sender, name, user_state, phone_id, msg_type=None):
    if selected_option == "confirm":
        user_state["step"] = "end"
        update_user_state(sender, user_state)
        send_text_message(
            sender, phone_id,
            "Thank you! Your listing will be published soon."
        )
    else:
        user_state["step"] = "end"
        update_user_state(sender, user_state)
        send_text_message(
            sender, phone_id,
            "Your listing was cancelled. Type 'Hi' to start over."
        )
    return jsonify({"status": "ok"})

# Update action mapping for interactive handlers
action_mapping = {
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
    app.run(debug=True, host="0.0.0.0", port=5000)
