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

def send(message, recipient, phone_id):
    """Send message via WhatsApp API"""
    if not all([message, recipient, phone_id]):
        logger.error("Missing parameters in send()")
        return False

    url = f"https://graph.facebook.com/v13.0/{phone_id}/messages"
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

            # For text messages, get lowercase text body; else empty string
            msg = message.get("text", {}).get("body", "").strip().lower() if msg_type == "text" else ""

            # Load or initialize user state
            user_state = get_user_state(sender) or {}
            if "user" not in user_state:
                user_state["user"] = {"name": name}
            user_state["user_id"] = sender

            # Initialize step if not present
            step = user_state.get("step", "start")
            user_state["step"] = step  # ensure step is set

            # Handle greetings anywhere
            if msg in ["hi", "hie", "hey"]:
                reply = "Hello! Are you a *student* or a *landlord*? Please reply with one."
                user_state["step"] = "start"
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

            # START step: choose landlord or student
            if step == "start":
                if msg == "landlord":
                    reply = "Great! Please send a screenshot of your WhatsApp username with your contact name for verification."
                    user_state["step"] = "awaiting_image"
                    update_user_state(sender, user_state)
                    send(reply, sender, phone_id)
                    return jsonify({"status": "ok"}), 200

                elif msg == "student":
                    reply = "Welcome, student! Please download our app to secure your accommodation."
                    user_state["step"] = "student_pending"
                    update_user_state(sender, user_state)
                    send(reply, sender, phone_id)
                    return jsonify({"status": "ok"}), 200

                else:
                    reply = "Please reply with *student* or *landlord*."
                    send(reply, sender, phone_id)
                    return jsonify({"status": "ok"}), 200

            # AWAITING IMAGE step - landlord sends image for verification
            elif step == "awaiting_image":
                if msg_type == "image":
                    reply = (
                        f"Thanks {name or 'there'} for the image.\n\n"
                        "Now let’s collect house details.\n\n"
                        "Do you have accommodation for *boys*, *girls*, or *mixed*?"
                    )
                    user_state["step"] = "approve_manual"
                    update_user_state(sender, user_state)
                    send(reply, sender, phone_id)
                    return jsonify({"status": "ok"}), 200
                else:
                    reply = "Please send a screenshot of your WhatsApp username with your contact name for verification."
                    send(reply, sender, phone_id)
                    return jsonify({"status": "ok"}), 200

            # APPROVE MANUAL step - ask house type
            elif step == "approve_manual":
                if msg in ["boys", "girls", "mixed"]:
                    user_state["house_type"] = msg
                    reply = "Do you have a *cat*? Please reply *yes* or *no*."
                    user_state["step"] = "ask_cat_owner"
                    update_user_state(sender, user_state)
                    send(reply, sender, phone_id)
                    return jsonify({"status": "ok"}), 200
                else:
                    reply = "Please reply with *boys*, *girls*, or *mixed*."
                    send(reply, sender, phone_id)
                    return jsonify({"status": "ok"}), 200

            # ASK CAT OWNER step
            elif step == "ask_cat_owner":
                if msg in ["yes", "no"]:
                    user_state["has_cat"] = msg
                    reply = "Do you have a vacancy? Reply *yes* or *no*."
                    user_state["step"] = "ask_availability"
                else:
                    reply = "Do you have a cat? Please reply *yes* or *no*."
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

            # ASK AVAILABILITY step
            elif step == "ask_availability":
                if msg == "no":
                    reply = "OK thanks. Whenever you have vacancies, don’t hesitate to say 'Hi!'"
                    user_state["step"] = "end"
                elif msg == "yes":
                    reply = "How many *boys* or *girls* do you need accommodation for in *single rooms*? (Enter number only)"
                    user_state["step"] = "ask_room_type"
                else:
                    reply = "Do you have a vacancy? Please reply *yes* or *no*."
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

            # ASK ROOM TYPE step (single rooms)
            elif step == "ask_room_type":
                if msg.isdigit():
                    user_state["room_single"] = int(msg)
                    reply = "Please confirm your rent for a single room (e.g. 130)."
                    user_state["step"] = "confirm_single"
                else:
                    reply = "Please enter the number of students needing single rooms (number only)."
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

            # CONFIRM SINGLE step (rent for single rooms)
            elif step == "confirm_single":
                try:
                    user_state["rent_single"] = float(msg)
                    reply = "How many students need 2-sharing rooms? (Enter number only)"
                    user_state["step"] = "ask_2_sharing"
                except ValueError:
                    reply = "Please enter the rent as a number (e.g. 130)."
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

            # ASK 2-SHARING step
            elif step == "ask_2_sharing":
                if msg.isdigit():
                    user_state["room_2_sharing"] = int(msg)
                    reply = "Please confirm your rent for 2-sharing rooms (e.g. 80)."
                    user_state["step"] = "confirm_2_sharing"
                else:
                    reply = "Please enter number of students needing 2-sharing rooms (number only)."
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

            # CONFIRM 2-SHARING step
            elif step == "confirm_2_sharing":
                try:
                    user_state["rent_2_sharing"] = float(msg)
                    reply = "How many students need 3-sharing rooms? (Enter number only)"
                    user_state["step"] = "ask_3_sharing"
                except ValueError:
                    reply = "Please enter the rent as a number (e.g. 80)."
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

            # ASK 3-SHARING step
            elif step == "ask_3_sharing":
                if msg.isdigit():
                    user_state["room_3_sharing"] = int(msg)
                    reply = "Please confirm your rent for 3-sharing rooms (e.g. 60)."
                    user_state["step"] = "confirm_3_sharing"
                else:
                    reply = "Please enter number of students needing 3-sharing rooms (number only)."
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

            # CONFIRM 3-SHARING step
            elif step == "confirm_3_sharing":
                try:
                    user_state["rent_3_sharing"] = float(msg)
                    reply = (
                        "Thank you! Your accommodation details are recorded.\n\n"
                        "Type 'Yes' to *list* this accommodation now, or 'No' to *cancel*."
                    )
                    user_state["step"] = "confirm_listing"
                except ValueError:
                    reply = "Please enter the rent as a number (e.g. 60)."
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

            # CONFIRM LISTING step
            elif step == "confirm_listing":
                if msg == "yes":
                    # Here you can add logic to save listing permanently
                    reply = "Your accommodation has been listed. Thank you!"
                    user_state["step"] = "end"
                elif msg == "no":
                    reply = "Your listing has been cancelled. If you want to start over, type 'Hi'."
                    user_state["step"] = "end"
                else:
                    reply = "Please reply with *Yes* or *No*."
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

            # END or unknown steps
            else:
                reply = "Thank you for using our service. If you want to start again, please say 'Hi'."
                user_state["step"] = "start"
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

        except Exception as e:
            logger.error(f"Exception in webhook processing: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)
