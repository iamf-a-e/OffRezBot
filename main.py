import os
import json
import requests
import logging
from flask import Flask, request, jsonify, render_template
import google.generativeai as genai
from redis_utils import get_user_state, update_user_state, save_user_state, is_duplicate_message

# Flask app
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

# Environment variables
WA_TOKEN = os.environ.get("WA_TOKEN")
phone_id = os.environ.get("PHONE_ID")
owner_phone = os.environ.get("OWNER_PHONE")
genai.configure(api_key=os.environ.get("GEN_API"))  # Gemini API

# Send message to WhatsApp
def send(message, recipient, phone_id):
    if not all([message, recipient, phone_id]):
        logger.error("Missing parameters in send()")
        return False

    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
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
        logger.info(f"Sent to {recipient}: {message}")
        return True
    except Exception as e:
        logger.error(f"Failed to send to {recipient}: {e}")
        return False

# Home route
@app.route("/", methods=["GET"])
def index():
    return render_template("connected.html")

# Webhook route
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
            message_id = message.get("id")

            if not sender or not message_id:
                return jsonify({"status": "error", "message": "Missing sender or message ID"}), 400

            if is_duplicate_message(sender, message_id):
                logger.info(f"Duplicate message ignored: {message_id}")
                return jsonify({"status": "ok", "message": "Duplicate ignored"}), 200

            name = value.get("contacts", [{}])[0].get("profile", {}).get("name", "")
            msg_type = message.get("type")
            msg = message.get("text", {}).get("body", "").strip().lower() if msg_type == "text" else ""

            # Load or init user state
            user_state = get_user_state(sender) or {}
            if "user" not in user_state:
                user_state["user"] = {"name": name}
            user_state["user_id"] = sender
            step = user_state.get("step", "start")

            # Greetings
            if msg in ["hi", "hie", "hey"]:
                reply = "Hello! Are you a *student* or a *landlord*? Please reply with one."
                user_state["step"] = "start"
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200


                if msg == "landlord":
                    reply = "Great! Please upload an image of your property to begin."
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

            # Handle image uploads
            if msg_type == "image":
                media_id = message["image"].get("id")
                if not media_id:
                    return jsonify({"status": "error", "message": "Missing image ID"}), 400

                if step != "approve_manual":
                    reply = (
                        f"Thanks {name or 'there'}. Approval will be done manually for security.\n\n"
                        "Now let’s collect house details.\n\n"
                        "Do you have accommodation for *boys*, *girls*, or *mixed*?"
                    )
                    user_state["step"] = "approve_manual"
                    update_user_state(sender, user_state)
                    send(reply, sender, phone_id)
                    return jsonify({"status": "ok"}), 200

            # Step-by-step logic
            if step == "approve_manual":
                if msg in ["boys", "girls", "mixed"]:
                    user_state["house_type"] = msg
                    reply = "Do you have a *cat*? Please reply *yes* or *no*."
                    user_state["step"] = "ask_cat_owner"
                else:
                    reply = "Please reply with *boys*, *girls*, or *mixed*."

            elif step == "ask_cat_owner":
                if msg in ["yes", "no"]:
                    user_state["has_cat"] = msg
                    reply = "Do you have a vacancy? Reply *yes* or *no*."
                    user_state["step"] = "ask_availability"
                else:
                    reply = "Do you have a cat? Please reply *yes* or *no*."

            elif step == "ask_availability":
                if msg == "no":
                    reply = "OK thanks. Whenever you have vacancies, don’t hesitate to say 'Hi!'"
                    user_state["step"] = "end"
                elif msg == "yes":
                    reply = "How many *boys* or *girls* do you need accommodation for in *single rooms*? (Enter number only)"
                    user_state["step"] = "ask_room_type"
                else:
                    reply = "Do you have a vacancy? Please reply *yes* or *no*."

            elif step == "ask_room_type":
                if msg.isdigit():
                    user_state["room_single"] = int(msg)
                    reply = "Please confirm your rent for a single room (e.g. 130)."
                    user_state["step"] = "confirm_single"
                else:
                    reply = "Please enter number of students needing single rooms (number only)."

            elif step == "confirm_single":
                try:
                    user_state["rent_single"] = float(msg)
                    reply = "How many students need 2-sharing rooms? (Enter number only)"
                    user_state["step"] = "ask_2_sharing"
                except ValueError:
                    reply = "Please enter the rent as a number (e.g. 130)."

            elif step == "ask_2_sharing":
                if msg.isdigit():
                    user_state["room_2_sharing"] = int(msg)
                    reply = "Please confirm your rent for 2-sharing rooms (e.g. 80)."
                    user_state["step"] = "confirm_2_sharing"
                else:
                    reply = "Please enter number of students needing 2-sharing rooms (number only)."

            elif step == "confirm_2_sharing":
                try:
                    user_state["rent_2_sharing"] = float(msg)
                    reply = "How many students need 3-sharing rooms? (Enter number only)"
                    user_state["step"] = "ask_3_sharing"
                except ValueError:
                    reply = "Please enter the rent as a number (e.g. 80)."

            elif step == "ask_3_sharing":
                if msg.isdigit():
                    user_state["room_3_sharing"] = int(msg)
                    reply = "Please confirm your rent for 3-sharing rooms (e.g. 60)."
                    user_state["step"] = "confirm_3_sharing"
                else:
                    reply = "Please enter number of students needing 3-sharing rooms (number only)."

            elif step == "confirm_3_sharing":
                try:
                    user_state["rent_3_sharing"] = float(msg)
                    reply = "What age group are the students? (e.g. 18-22)"
                    user_state["step"] = "ask_student_age"
                except ValueError:
                    reply = "Please enter the rent as a number (e.g. 60)."

            elif step == "ask_student_age":
                user_state["student_age"] = msg
                reply = "Thank you. Please confirm your listing by typing *confirm* or type *cancel* to abort."
                user_state["step"] = "confirm_listing"

            elif step == "confirm_listing":
                if msg == "confirm":
                    reply = "Thank you! Your listing will be published soon."
                    user_state["step"] = "end"
                    save_user_state(sender, user_state)
                elif msg == "cancel":
                    reply = "Your listing was cancelled. Type 'Hi' to start over."
                    user_state["step"] = "end"
                    save_user_state(sender, user_state)
                else:
                    reply = "Please type *confirm* to publish your listing or *cancel* to abort."

            elif step == "end":
                if msg in ["hi", "hie", "hey"]:
                    reply = "Welcome back! Are you a *student* or a *landlord*?"
                    user_state["step"] = "start"
                else:
                    reply = "Thank you for contacting us. Type 'Hi' to start again."

            else:
                reply = "Sorry, I did not understand that. Please try again."

            update_user_state(sender, user_state)
            send(reply, sender, phone_id)
            return jsonify({"status": "ok"}), 200

        except Exception as e:
            logger.exception("Webhook error")
            return jsonify({"status": "error", "message": str(e)}), 500
