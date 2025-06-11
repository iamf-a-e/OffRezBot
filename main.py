import os
import json
import requests
import logging
from flask import Flask, request, jsonify, render_template
import google.generativeai as genai

app = Flask(_name_)

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

def send(message, recipient, phone_id):
    """Send message via WhatsApp API with improved error handling"""
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
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        logger.info(f"Sent message to {recipient}: {message}")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send message to {recipient}: {str(e)}")
        if hasattr(e, 'response') and e.response:
            logger.error(f"Response content: {e.response.text}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending message: {str(e)}")
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
        try:
            data = request.get_json()
            if not data:
                logger.error("Empty request body received")
                return jsonify({"status": "error", "message": "Empty request"}), 400

            logger.info(f"Incoming webhook data: {json.dumps(data, indent=2)}")

            entry = data.get("entry", [{}])[0]
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

            # Get user info safely with defaults
            contacts = value.get("contacts", [{}])
            profile = contacts[0].get("profile", {}) if contacts else {}
            name = profile.get("name", "")
            
            msg_type = message.get("type")
            msg_body = message.get("text", {}).get("body", "").strip().lower() if msg_type == "text" else ""

            # Initialize user state with thread safety
            try:
                user_state = get_user_state(sender) or {}
                if "user" not in user_state:
                    user_state["user"] = {"name": name}
                user_state["user_id"] = sender
                current_step = user_state.get("step", "start")
            except Exception as e:
                logger.error(f"Error getting user state: {str(e)}")
                return jsonify({"status": "error", "message": "Internal server error"}), 500

            # Handle different message types
            if msg_type == "image":
                return handle_awaiting_image(msg_body, msg_type, sender, name, user_state, phone_id)
            
            # Route to appropriate handler based on current step
            handler = action_mapping.get(current_step, handle_default)
            return handler(msg_body, sender, name, user_state, phone_id)

        except Exception as e:
            logger.exception(f"Unhandled error in webhook: {str(e)}")
            try:
                if sender:
                    send("Sorry, something went wrong. Please try again later.", sender, phone_id)
            except:
                pass
            return jsonify({"status": "error", "message": "Internal server error"}), 500


def handle_start(msg, sender, name, user_state, phone_id):
    try:
        msg = msg.lower() if msg else ""

        if msg in ["hi", "hie", "hey", "hello"]:
            reply = "Hello! Are you a student or a landlord? Please reply with one."
        elif msg == "landlord":
            reply = "Great! Please send a screenshot of your WhatsApp username with your contact name for verification."
            user_state["step"] = "awaiting_image"
        elif msg == "student":
            reply = "Welcome, student! Please download our app to secure your accommodation."
            user_state["step"] = "student_pending"
        else:
            reply = "Please reply with student or landlord to continue."

        update_user_state(sender, user_state)
        send(reply, sender, phone_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Error in handle_start: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_awaiting_image(msg, msg_type, sender, name, user_state, phone_id):
    try:
        if msg_type == "image":
            reply = (
                f"Thanks {name or 'there'} for the image.\n\n"
                "Now let's collect house details.\n\n"
                "Do you have accommodation for boys, girls, or mixed?"
            )
            user_state["step"] = "manual"
        else:
            reply = "Please send an image (a screenshot of your WhatsApp profile name with your contact name) to proceed."

        update_user_state(sender, user_state)
        send(reply, sender, phone_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Error in handle_awaiting_image: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_manual_house_type(msg, sender, name, user_state, phone_id):
    try:
        msg = msg.lower() if msg else ""

        if msg in ["boys", "girls", "mixed"]:
            user_state["house_type"] = msg
            reply = "Do you have a cat? Please reply yes or no."
            user_state["step"] = "ask_cat_owner"
        else:
            reply = "Please reply with boys, girls, or mixed."

        update_user_state(sender, user_state)
        send(reply, sender, phone_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Error in handle_manual_house_type: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_ask_cat_owner(msg, sender, name, user_state, phone_id):
    try:
        msg = msg.lower() if msg else ""

        if msg in ["yes", "no"]:
            user_state["has_cat"] = msg
            reply = "Do you have a vacancy? Reply yes or no."
            user_state["step"] = "ask_availability"
        else:
            reply = "Do you have a cat? Please reply yes or no."

        update_user_state(sender, user_state)
        send(reply, sender, phone_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Error in handle_ask_cat_owner: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_ask_availability(msg, sender, name, user_state, phone_id):
    try:
        msg = msg.lower() if msg else ""

        if msg == "no":
            reply = "OK thanks. Whenever you have vacancies, don't hesitate to say 'Hi!'"
            user_state["step"] = "end"
        elif msg == "yes":
            reply = "How many boys or girls do you need accommodation for in single rooms? (Enter number only)"
            user_state["step"] = "ask_room_type"
        else:
            reply = "Do you have a vacancy? Please reply yes or no."

        update_user_state(sender, user_state)
        send(reply, sender, phone_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Error in handle_ask_availability: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_ask_single_room_count(msg, sender, name, user_state, phone_id):
    try:
        if msg.isdigit():
            user_state["room_single"] = int(msg)
            reply = "Please confirm your rent for a single room (e.g. 130)."
            user_state["step"] = "confirm_single"
        else:
            reply = "Please enter the number of students needing single rooms (number only)."

        update_user_state(sender, user_state)
        send(reply, sender, phone_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Error in handle_ask_single_room_count: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_confirm_single_rent(msg, sender, name, user_state, phone_id):
    try:
        if msg.replace('.', '', 1).isdigit():  # Check for valid number
            user_state["rent_single"] = float(msg)
            reply = "How many students need 2-sharing rooms? (Enter number only)"
            user_state["step"] = "ask_2_sharing"
        else:
            reply = "Please enter the rent as a number (e.g. 130)."

        update_user_state(sender, user_state)
        send(reply, sender, phone_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Error in handle_confirm_single_rent: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_ask_2_sharing_count(msg, sender, name, user_state, phone_id):
    try:
        if msg.isdigit():
            user_state["room_2_sharing"] = int(msg)
            reply = "Please confirm your rent for 2-sharing rooms (e.g. 80)."
            user_state["step"] = "confirm_2_sharing"
        else:
            reply = "Please enter number of students needing 2-sharing rooms (number only)."

        update_user_state(sender, user_state)
        send(reply, sender, phone_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Error in handle_ask_2_sharing_count: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_confirm_2_sharing_rent(msg, sender, name, user_state, phone_id):
    try:
        if msg.replace('.', '', 1).isdigit():  # Check for valid number
            user_state["rent_2_sharing"] = float(msg)
            reply = "How many students need 3-sharing rooms? (Enter number only)"
            user_state["step"] = "ask_3_sharing"
        else:
            reply = "Please enter the rent as a number (e.g. 80)."

        update_user_state(sender, user_state)
        send(reply, sender, phone_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Error in handle_confirm_2_sharing_rent: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_ask_3_sharing_count(msg, sender, name, user_state, phone_id):
    try:
        if msg.isdigit():
            user_state["room_3_sharing"] = int(msg)
            reply = "Please confirm your rent for 3-sharing rooms (e.g. 60)."
            user_state["step"] = "confirm_3_sharing"
        else:
            reply = "Please enter number of students needing 3-sharing rooms (number only)."

        update_user_state(sender, user_state)
        send(reply, sender, phone_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Error in handle_ask_3_sharing_count: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_confirm_3_sharing_rent(msg, sender, name, user_state, phone_id):
    try:
        if msg.replace('.', '', 1).isdigit():  # Check for valid number
            user_state["rent_3_sharing"] = float(msg)
            reply = "What age group are the students? (e.g. 18-22)"
            user_state["step"] = "ask_student_age"
        else:
            reply = "Please enter the rent as a number (e.g. 60)."

        update_user_state(sender, user_state)
        send(reply, sender, phone_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Error in handle_confirm_3_sharing_rent: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_ask_student_age(msg, sender, name, user_state, phone_id):
    try:
        user_state["student_age"] = msg
        reply = "Thank you. Please confirm your listing by typing confirm or type cancel to abort."
        user_state["step"] = "confirm_listing"

        update_user_state(sender, user_state)
        send(reply, sender, phone_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Error in handle_ask_student_age: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_confirm_listing(msg, sender, name, user_state, phone_id):
    try:
        msg = msg.lower() if msg else ""

        if msg == "confirm":
            # Here you would typically save the complete listing to your database
            reply = "Thank you! Your listing will be published soon."
            user_state["step"] = "end"
            
            # Optional: Send confirmation to owner
            if owner_phone:
                owner_msg = f"New listing from {name} ({sender}):\n"
                owner_msg += f"House Type: {user_state.get('house_type')}\n"
                owner_msg += f"Has Cat: {user_state.get('has_cat')}\n"
                owner_msg += f"Single Rooms: {user_state.get('room_single')} @ {user_state.get('rent_single')}\n"
                owner_msg += f"2-Sharing: {user_state.get('room_2_sharing')} @ {user_state.get('rent_2_sharing')}\n"
                owner_msg += f"3-Sharing: {user_state.get('room_3_sharing')} @ {user_state.get('rent_3_sharing')}\n"
                owner_msg += f"Age Group: {user_state.get('student_age')}"
                send(owner_msg, owner_phone, phone_id)
                
        elif msg == "cancel":
            reply = "Your listing was cancelled. Type 'Hi' to start over."
            user_state["step"] = "end"
        else:
            reply = "Please type confirm to publish your listing or cancel to abort."

        update_user_state(sender, user_state)
        send(reply, sender, phone_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Error in handle_confirm_listing: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_student_pending(msg, sender, name, user_state, phone_id):
    try:
        reply = "Please download our app to secure your accommodation."
        update_user_state(sender, user_state)
        send(reply, sender, phone_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Error in handle_student_pending: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_end(msg, sender, name, user_state, phone_id):
    try:
        msg = msg.lower() if msg else ""

        if msg in ["hi", "hie", "hey", "hello"]:
            reply = "Welcome back! Are you a student or a landlord?"
            user_state["step"] = "start"
        else:
            reply = "Thank you for contacting us. Type 'Hi' to start again."

        update_user_state(sender, user_state)
        send(reply, sender, phone_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Error in handle_end: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_default(msg, sender, name, user_state, phone_id):
    try:
        reply = "Sorry, I didn't understand that. Type 'Hi' to start over."
        user_state["step"] = "start"
        update_user_state(sender, user_state)
        send(reply, sender, phone_id)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Error in handle_default: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


# Action mapping with all handlers
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

