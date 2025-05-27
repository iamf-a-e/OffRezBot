import os
import json
import time
import requests
import logging
import random
import string
from datetime import datetime
from flask import Flask, request, jsonify, render_template


logging.basicConfig(level=logging.INFO)

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

def save_user_state(user_id, state, expiry_seconds=60):
    redis_client.set(f"user_state:{user_id}", json.dumps(state), ex=expiry_seconds)

def get_user_state(user_id):
    result = redis_client.get(f"user_state:{user_id}")
    if result:
        return json.loads(result)
    return None

# ==================== Messaging Logic ====================
def generate_confirmation_message(
    student,
    *,
    semester="current",
    mass_messaging=False,
    initiated_by="placement_team"
):
    name = student.get("name", "the student")
    gender = student.get("gender", "male").lower()
    pronoun = "he" if gender == "male" else "she"
    part = student.get("part", "")
    room_type = student.get("room_type", "")
    budget = student.get("budget", "")
    city = student.get("city", "")
    house_name = student.get("house_name", "")
    move_in_date = student.get("move_in_date", "")
    multiple_houses = student.get("multiple_houses", False)
    landlord_name = student.get("landlord_name", "")

    if initiated_by == "landlord":
        intro = f"Hello {landlord_name}, thank you for reaching out! 👋\n"
        detail = (
            "How can we help you today regarding your student accommodation?\n"
            "If you have vacancies or updates, please let us know below. 🏠"
        )
        return intro + detail

    if mass_messaging:
        if semester == "current":
            msg = (
                f"Hi {landlord_name} 👋, this is Talent from the student accommodation placement team.\n\n"
                f"We are confirming available rooms for the **current semester** in your house(s) in {city}.\n"
                "Do you have any vacancies?\n\n"
                "Please reply with:\n"
                "✅ Yes - if you have available rooms\n"
                "❌ No - if you can't take students\n"
                "🏠 Full - if your house(s) are currently full"
            )
        else:
            msg = (
                f"Hi {landlord_name} 👋, this is Talent from the student accommodation placement team.\n\n"
                f"We are planning ahead for the **next semester** and would like to know if you'll have vacancies in your house(s) in {city}.\n"
                "Do you expect to have any available rooms for students next semester?\n\n"
                "Please reply with:\n"
                "✅ Yes - if you'll have rooms\n"
                "❌ No - if you won't have rooms\n"
                "🏠 Full - if your house(s) will be full"
            )
        return msg

    intro = f"Hello {landlord_name} 👋, this is Talent from the student accommodation placement team."
    if multiple_houses:
        intro += f" You have more than one house in {city}, so I need to confirm a student directly for one of your houses."
    if semester == "next":
        intro += " (Next Semester Vacancy Confirmation)"

    detail = (
        f"\n\nStudent: {name}\n"
        f"Part: {part}\n"
        f"Room Type: {room_type}\n"
        f"Gender: {gender}\n"
        f"City: {city}\n"
        f"Preferred House: {house_name}\n"
        f"Budget: ${budget}\n"
        f"Move-in: {move_in_date}"
    )

    confirmation = (
        "\n\nIs there a room available at your place for this student?\n"
        "Please reply with:\n"
        "✅ Yes - if there's a room\n"
        "❌ No - if you can't take this student\n"
        "🏠 Full - if the house is currently full"
    )

    return intro + detail + confirmation

def handle_landlord_reply(reply, student, *, context="direct"):
    reply = reply.strip().lower()
    name = student.get("name", "the student")
    if reply in ["yes", "✅"]:
        if context == "landlord_initiated":
            return "Thank you for letting us know about your vacancy! We'll reach out if we have students looking for accommodation. 🏠"
        elif context.startswith("mass"):
            return "Thank you for confirming you have available rooms. We'll be in touch with student matches soon. 👍"
        else:
            return f"Great! I'll let {name} know that a room is available and proceed with confirmation. 🎉"
    elif reply in ["no", "❌"]:
        if context == "landlord_initiated":
            return "Noted. If you have any updates or future vacancies, please let us know. 🙏"
        elif context.startswith("mass"):
            return "Noted. We won't assign students to your house(s) for this semester. Let us know if anything changes."
        else:
            return (f"Noted. We’ll inform {name} that the place is not available.\n"
                    "Let us know if anything changes. 🙏")
    elif reply in ["full", "🏠"]:
        if context == "landlord_initiated":
            return "Thanks for the update! We'll mark your house(s) as full for now."
        elif context.startswith("mass"):
            return "Thanks for letting us know your house(s) are full. We'll update our records."
        else:
            return ("Okay, we’ll mark the house as full for now and not assign more students.\n"
                    "Thanks for the update! 🏠")
    else:
        return "Sorry, I didn’t understand your response. Please reply with ✅ Yes, ❌ No, or 🏠 Full."

# ==================== Flask Webhook Configuration ====================
app = Flask(__name__)

@app.route("/", methods=["GET"])
def index():
    return "OffRezBot API is running.", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json if request.is_json else request.form.to_dict()
    user_id = data.get("user_id") or "default_user"
    event_type = data.get("event_type", "message")
    # Load or initialize user state
    user_state = get_user_state(user_id) or {}

    # Example: handle a landlord reply via webhook
    if event_type == "landlord_reply":
        reply = data.get("reply", "")
        context = data.get("context", "direct")
        response = handle_landlord_reply(reply, user_state, context=context)
        # Update state with reply and refresh expiry
        user_state["last_reply"] = reply
        save_user_state(user_id, user_state, expiry_seconds=60)
        return jsonify({
            "status": "ok",
            "reply": response,
            "state": user_state
        })

    # Example: send confirmation message
    if event_type == "send_confirmation":
        semester = data.get("semester", "current")
        mass_messaging = str(data.get("mass_messaging", "false")).lower() == "true"
        initiated_by = data.get("initiated_by", "placement_team")
        # update user state if new student info is provided
        if "student_info" in data:
            user_state.update(data["student_info"])
            save_user_state(user_id, user_state, expiry_seconds=60)
        confirmation = generate_confirmation_message(
            user_state,
            semester=semester,
            mass_messaging=mass_messaging,
            initiated_by=initiated_by
        )
        return jsonify({
            "status": "ok",
            "confirmation_message": confirmation,
            "state": user_state
        })

    return jsonify({"status": "error", "message": "Unsupported event_type"}), 400

@app.route("/webhook", methods=["GET"])
def webhook_get():
    """GET handler for health checks or simple state inspection"""
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"status": "error", "message": "Missing user_id parameter"}), 400

    user_state = get_user_state(user_id)
    if user_state is None:
        return jsonify({"status": "error", "message": "No state found for user"}), 404

    return jsonify({
        "status": "ok",
        "user_id": user_id,
        "state": user_state
    })

# ==================== CLI/Test Block ====================
if __name__ == "__main__":
    # CLI/test for local development
    semester = os.environ.get("SEMESTER", "current")
    mass_messaging = os.environ.get("MASS_MESSAGING", "false").lower() == "true"
    initiated_by = os.environ.get("INITIATED_BY", "placement_team")

    student_info = {
        "name": os.environ.get("STUDENT_NAME", "Sarah Mahombe"),
        "gender": os.environ.get("STUDENT_GENDER", "female"),
        "part": os.environ.get("STUDENT_PART", "1.1"),
        "room_type": os.environ.get("STUDENT_ROOM_TYPE", "2-sharing"),
        "budget": int(os.environ.get("STUDENT_BUDGET", 120)),
        "city": os.environ.get("STUDENT_CITY", "Harare"),
        "house_name": os.environ.get("STUDENT_HOUSE_NAME", "Rosewood Villa"),
        "move_in_date": os.environ.get("STUDENT_MOVE_IN_DATE", "June 1"),
        "multiple_houses": os.environ.get("MULTIPLE_HOUSES", "true").lower() == "true",
        "landlord_name": os.environ.get("LANDLORD_NAME", "Mr. Nyasha")
    }

    user_id = os.environ.get("USER_ID", "test_user_1")

    # Save initial user state with expiry
    save_user_state(user_id, student_info, expiry_seconds=60)
    print(f"Saved user state for {user_id} (expires after 60 seconds idle).")

    # Retrieve and print user state
    loaded_state = get_user_state(user_id)
    print(f"Loaded user state: {loaded_state}")

    # Demonstrate confirmation message using loaded state
    print("\n== Confirmation Message ==")
    print(generate_confirmation_message(
        loaded_state,
        semester=semester,
        mass_messaging=mass_messaging,
        initiated_by=initiated_by
    ))
    print()

    # Simulate a landlord reply and update user session expiry
    reply = "Yes"
    context = "direct"
    response = handle_landlord_reply(reply, loaded_state, context=context)
    print(f"Landlord reply ({reply}): {response}")

    # Update and save new state, resetting expiry
    loaded_state["last_reply"] = reply
    save_user_state(user_id, loaded_state, expiry_seconds=60)
    print(f"Updated user state for {user_id} (expiry refreshed).")

    # To run the webhook server:
    # export FLASK_APP=main.py && flask run --port 5000
    # Or simply:
    # python main.py
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
