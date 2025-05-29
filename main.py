import os
import redis
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v17.0"
wa_token = os.getenv("WHATSAPP_TOKEN")  # Your WhatsApp token here
phone_id = os.getenv("WHATSAPP_PHONE_ID")  # Your WhatsApp phone number ID
redis_client = redis.Redis.from_url(
    url=os.getenv("REDIS_URL"),
    password=os.getenv("REDIS_TOKEN"),
    ssl=True
)

# Send message helper
def send(text, recipient_id, phone_id):
    url = f"https://graph.facebook.com/v17.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {wa_token}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": recipient_id,
        "text": {"body": text}
    }
    resp = requests.post(url, headers=headers, json=data)
    return resp

# Redis state helpers
def update_user_state(user_id, state):
    redis_client.set(f"user_state:{user_id}", str(state), ex=86400)

def save_user_state(user_id, state):
    redis_client.set(f"user_final_state:{user_id}", str(state))

def get_user_state(user_id):
    state_str = redis_client.get(f"user_state:{user_id}")
    if state_str:
        return eval(state_str)
    return {"step": "start"}

# Landlord flow logic (from your provided steps)
def landlord_flow(sender, phone_id, message):
    if message.get("type") != "text":
        send("Sorry, please send text messages only.", sender, phone_id)
        return jsonify({"status": "ok"}), 200

    msg = message.get("text", {}).get("body", "").strip().lower()
    user_state = get_user_state(sender)
    step = user_state.get("step", "approve_manual")

    if step == "start":
        reply = "Hello! Are you a *student* or a *landlord*? Please reply with one."
        user_state["step"] = "approve_manual"
    
    elif step == "approve_manual":
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
            reply = "Please enter the number of students needing single rooms (number only)."

    elif step == "confirm_single":
        try:
            rent_single = float(msg)
            user_state["rent_single"] = rent_single
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
            rent_2_sharing = float(msg)
            user_state["rent_2_sharing"] = rent_2_sharing
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
            rent_3_sharing = float(msg)
            user_state["rent_3_sharing"] = rent_3_sharing
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
            reply = "Thank you! Your listing will be published soon. Expect calls from students if you have vacancies."
            user_state["step"] = "end"
            save_user_state(sender, user_state)
        elif msg == "cancel":
            reply = "Your listing was cancelled. Type 'Hi' to start over."
            user_state["step"] = "end"
            save_user_state(sender, user_state)
        else:
            reply = "Please type *confirm* to publish your listing or *cancel* to abort."

    elif step == "end":
        if msg == "hi":
            reply = "Hello! Are you a *student* or a *landlord*? Please reply with one."
            user_state["step"] = "approve_manual"
            save_user_state(sender, user_state)
        else:
            reply = "Thank you for contacting us. Type 'Hi' if you want to start again."

    else:
        reply = "Sorry, I did not understand that. Please try again."

    send(reply, sender, phone_id)
    update_user_state(sender, user_state)
    return jsonify({"status": "ok"}), 200

# Student flow: just ask to download app
def student_flow(sender, phone_id):
    reply = (
        "Hi! To find the best hostels, please download our app here:\n"
        "https://example.com/download\n\n"
        "If you have questions, just reply here."
    )
    send(reply, sender, phone_id)
    # Clear or reset state for students (optional)
    redis_client.delete(f"user_state:{sender}")
    return jsonify({"status": "ok"}), 200

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if not data:
        return jsonify({"status": "no data"}), 400

    # Parse incoming WhatsApp webhook format
    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        messages = value.get("messages", [])
        if not messages:
            return jsonify({"status": "no messages"}), 200

        message = messages[0]
        sender = message["from"]
    except (KeyError, IndexError):
        return jsonify({"status": "bad format"}), 400

    # Determine user type by stored state or by initial greeting message
    user_state = get_user_state(sender)
    user_type = user_state.get("user_type")

    # If user type unknown, check greeting message
    if not user_type:
        msg_text = message.get("text", {}).get("body", "").strip().lower()
        if msg_text in ["student", "landlord"]:
            user_type = msg_text
            user_state["user_type"] = user_type
            user_state["step"] = "approve_manual" if user_type == "landlord" else "start"
            update_user_state(sender, user_state)
        else:
            # Ask for role
            send("Welcome! Are you a *student* or a *landlord*? Please reply with one.", sender, phone_id)
            return jsonify({"status": "ok"}), 200

    # Route to proper flow
    if user_type == "student":
        return student_flow(sender, phone_id)
    elif user_type == "landlord":
        return landlord_flow(sender, phone_id, message)
    else:
        send("Sorry, I didn't understand. Please reply with *student* or *landlord*.", sender, phone_id)
        return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(port=5000, debug=True)
