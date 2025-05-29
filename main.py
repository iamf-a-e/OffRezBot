import os
import json
from flask import Flask, request, jsonify
import redis
import requests

app = Flask(__name__)

# Environment variables
REDIS_URL = os.getenv("REDIS_URL")
PHONE_ID = os.getenv("PHONE_ID")
WA_TOKEN = os.getenv("WA_TOKEN")  # WhatsApp Bearer Token

if not REDIS_URL or not PHONE_ID or not WA_TOKEN:
    raise Exception("Set REDIS_URL, PHONE_ID, and WA_TOKEN environment variables")

redis_client = redis.Redis.from_url(REDIS_URL, ssl=True)

GRAPH_API_BASE = "https://graph.facebook.com/v17.0"

def get_user_state(user_id):
    state = redis_client.get(user_id)
    if state:
        return json.loads(state.decode("utf-8"))
    return {}

def save_user_state(user_id, state_dict):
    redis_client.set(user_id, json.dumps(state_dict))

def send(message, to, phone_id):
    # Send a WhatsApp text message via WhatsApp Cloud API
    url = f"https://graph.facebook.com/v17.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "text": {"body": message}
    }
    resp = requests.post(url, headers=headers, json=data)
    # Optional: you can handle errors/log here if you want
    return resp.status_code == 200

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    entry = data.get("entry", [])[0]
    changes = entry.get("changes", [])[0]
    value = changes.get("value", {})
    messages = value.get("messages", [])

    if not messages:
        return jsonify({"status": "no_message"}), 200

    message = messages[0]
    sender = message.get("from")
    msg_type = message.get("type")

    user_state = get_user_state(sender)
    step = user_state.get("step", "start")

    # Extract text message body if text
    if msg_type == "text":
        msg = message.get("text", {}).get("body", "").strip().lower()

        # Starting point: ask if student or landlord
        if step == "start":
            if msg == "landlord":
                reply = "Please specify your house type: boys, girls, or mixed."
                user_state["step"] = "approve_manual"
            elif msg == "student":
                reply = "Please download our app here: https://example.com/app"
                user_state["step"] = "end"
            else:
                reply = "Hello! Are you a *student* or a *landlord*? Please reply with one."
                user_state["step"] = "start"

            send(reply, sender, PHONE_ID)
            save_user_state(sender, user_state)
            return jsonify({"status": "ok"}), 200

        # Landlord flow starts here
        if step == "approve_manual":
            if msg in ["boys", "girls", "mixed"]:
                user_state["house_type"] = msg
                reply = "Do you have a *cat*? Please reply *yes* or *no*."
                user_state["step"] = "ask_cat_owner"
            else:
                reply = "Please reply with *boys*, *girls*, or *mixed*."

            send(reply, sender, PHONE_ID)
            save_user_state(sender, user_state)
            return jsonify({"status": "ok"}), 200

        if step == "ask_cat_owner":
            if msg in ["yes", "no"]:
                user_state["has_cat"] = msg
                reply = "Do you have a vacancy? Reply *yes* or *no*."
                user_state["step"] = "ask_availability"
            else:
                reply = "Do you have a cat? Please reply *yes* or *no*."

            send(reply, sender, PHONE_ID)
            save_user_state(sender, user_state)
            return jsonify({"status": "ok"}), 200

        if step == "ask_availability":
            if msg == "no":
                reply = "OK thanks. Whenever you have vacancies, don’t hesitate to say 'Hi!'"
                user_state["step"] = "end"
            elif msg == "yes":
                reply = "How many *boys* or *girls* do you need accommodation for in *single rooms*? (Enter number only)"
                user_state["step"] = "ask_room_type"
            else:
                reply = "Do you have a vacancy? Please reply *yes* or *no*."

            send(reply, sender, PHONE_ID)
            save_user_state(sender, user_state)
            return jsonify({"status": "ok"}), 200

        if step == "ask_room_type":
            if msg.isdigit():
                user_state["room_single"] = int(msg)
                reply = "Please confirm your rent for a single room (e.g. 130)."
                user_state["step"] = "confirm_single"
            else:
                reply = "Please enter the number of students needing single rooms (number only)."

            send(reply, sender, PHONE_ID)
            save_user_state(sender, user_state)
            return jsonify({"status": "ok"}), 200

        if step == "confirm_single":
            try:
                rent_single = float(msg)
                user_state["rent_single"] = rent_single
                reply = "How many students need 2-sharing rooms? (Enter number only)"
                user_state["step"] = "ask_2_sharing"
            except ValueError:
                reply = "Please enter the rent as a number (e.g. 130)."

            send(reply, sender, PHONE_ID)
            save_user_state(sender, user_state)
            return jsonify({"status": "ok"}), 200

        if step == "ask_2_sharing":
            if msg.isdigit():
                user_state["room_2_sharing"] = int(msg)
                reply = "Please confirm your rent for 2-sharing rooms (e.g. 80)."
                user_state["step"] = "confirm_2_sharing"
            else:
                reply = "Please enter number of students needing 2-sharing rooms (number only)."

            send(reply, sender, PHONE_ID)
            save_user_state(sender, user_state)
            return jsonify({"status": "ok"}), 200

        if step == "confirm_2_sharing":
            try:
                rent_2_sharing = float(msg)
                user_state["rent_2_sharing"] = rent_2_sharing
                reply = "How many students need 3-sharing rooms? (Enter number only)"
                user_state["step"] = "ask_3_sharing"
            except ValueError:
                reply = "Please enter the rent as a number (e.g. 80)."

            send(reply, sender, PHONE_ID)
            save_user_state(sender, user_state)
            return jsonify({"status": "ok"}), 200

        if step == "ask_3_sharing":
            if msg.isdigit():
                user_state["room_3_sharing"] = int(msg)
                reply = "Please confirm your rent for 3-sharing rooms (e.g. 60)."
                user_state["step"] = "confirm_3_sharing"
            else:
                reply = "Please enter number of students needing 3-sharing rooms (number only)."

            send(reply, sender, PHONE_ID)
            save_user_state(sender, user_state)
            return jsonify({"status": "ok"}), 200

        if step == "confirm_3_sharing":
            try:
                rent_3_sharing = float(msg)
                user_state["rent_3_sharing"] = rent_3_sharing
                reply = "What age group are the students? (e.g. 18-22)"
                user_state["step"] = "ask_student_age"
            except ValueError:
                reply = "Please enter the rent as a number (e.g. 60)."

            send(reply, sender, PHONE_ID)
            save_user_state(sender, user_state)
            return jsonify({"status": "ok"}), 200

        if step == "ask_student_age":
            user_state["student_age"] = msg
            reply = "Thank you. Please confirm your listing by typing *confirm* or type *cancel* to abort."
            user_state["step"] = "confirm_listing"

            send(reply, sender, PHONE_ID)
            save_user_state(sender, user_state)
            return jsonify({"status": "ok"}), 200

        if step == "confirm_listing":
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
                # Don't change step

            send(reply, sender, PHONE_ID)
            save_user_state(sender, user_state)
            return jsonify({"status": "ok"}), 200

        if step == "end":
            if msg == "hi":
                reply = "Hello! Are you a *student* or a *landlord*? Please reply with one."
                user_state["step"] = "start"
                save_user_state(sender, user_state)
            else:
                reply = "Thank you for contacting us. Type 'Hi' if you want to start again."

            send(reply, sender, PHONE_ID)
            save_user_state(sender, user_state)
            return jsonify({"status": "ok"}), 200

        # Fallback unknown step
        reply = "Sorry, I did not understand that. Please try again."
        send(reply, sender, PHONE_ID)
        return jsonify({"status": "ok"}), 200

    else:
        # Non-text messages handler (optional)
        reply = "Sorry, I can only process text messages right now."
        send(reply, sender, PHONE_ID)
        return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(debug=True, port=5000)
