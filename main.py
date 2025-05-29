import os
import json
import requests
import logging
from flask import Flask, request, jsonify, render_template
import google.generativeai as genai
import base64

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
wa_token = os.environ.get("WA_TOKEN")  # WhatsApp API Key
WA_TOKEN = wa_token
phone_id = os.environ.get("PHONE_ID")
genai.configure(api_key=os.environ.get("GEN_API"))    # Gemini API Key
owner_phone = os.environ.get("OWNER_PHONE")
GRAPH_API_BASE = "https://graph.facebook.com/v19.0"

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

def advance(user_id, user_state, new_step, response=None):
   user_state["step"] = new_step
   save_user_state(user_id, user_state)
   return response, user_state
    
# Helper to check valid image extension
def is_image_extension(filename):
    allowed_exts = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}
    return os.path.splitext(fname)[1].lower() in allowed_exts


def message_handler(sender, message, user_state, value):
    user_id = user_state.get("user_id")  # Ensure user_id is present

    if not user_state:
        user_state = {"step": "start"}

    step = user_state.get("step", "start")

    # === Handle incoming image messages ===
    if isinstance(message, dict) and message.get("type") == "image":
        media_id = message["image"].get("id")
        if not media_id:
            send_message(sender, "Sorry, I couldn't get your image. Please try again.")
            return None, user_state

        # Get media URL from WhatsApp Graph API
        media_resp = requests.get(
            f"{GRAPH_API_BASE}/{media_id}",
            headers={"Authorization": f"Bearer {WA_TOKEN}"}
        )
        if media_resp.status_code != 200:
            send_message(sender, "Failed to get your image info. Please try again.")
            return None, user_state

        media_url = media_resp.json().get("url")
        if not media_url:
            send_message(sender, "Failed to get your image URL. Please try again.")
            return None, user_state

        # Download image bytes from media URL
        img_resp = requests.get(media_url, headers={"Authorization": f"Bearer {WA_TOKEN}"})
        if img_resp.status_code != 200:
            send_message(sender, "Failed to download your image. Please try again.")
            return None, user_state

        # Determine file extension from Content-Type
        content_type = img_resp.headers.get("Content-Type", "").lower()
        mime_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/bmp": ".bmp",
            "image/tiff": ".tiff",
            "image/webp": ".webp",
        }
        extension = mime_map.get(content_type, ".jpg")  # default to .jpg

        filename = f"{media_id}{extension}"

        if not is_image_extension(filename):
            send_message(sender, "Please upload a valid image file (jpg, png, gif, bmp, tiff, webp).")
            return None, user_state

        # Convert image bytes to base64 string
        image_base64 = base64.b64encode(img_resp.content).decode("utf-8")

        # Save image info in user_state
        user_state["image_base64"] = image_base64
        user_state["image_filename"] = filename
        save_user_state(sender, user_state)

        # Move conversation forward - example prompt
        reply_text, user_state = advance(
            sender,
            user_state,
            "approve_manual",
            "Thanks! Your image is received for manual approval.\n\n"
            "Next, please provide the house details.\n"
            "Do you have accommodation for *boys*, *girls*, or *mixed*?"
        )
        send(reply_text, sender, value.get("metadata", {}).get("phone_number_id"))
        return None, user_state

    # === Handle text messages ===
    if isinstance(message, str):
        msg = message.strip().lower()
    else:
        # Unexpected non-text, non-image message
        send_message(sender, "Sorry, I can only process text and images at the moment.")
        return None, user_state

    # === Conversation flow logic based on user state step ===

    # Start of conversation or restart
    if step == "start":
        if "student" in msg:
            reply, user_state = advance(
                sender,
                user_state,
                "student_redirect",
                "Please use the student app to check hostel availability: https://playstore.com/xyz"
            )
            return reply, user_state
        if "landlord" in msg:
            reply, user_state = advance(
                sender,
                user_state,
                "get_whatsapp_verification",
                "OK. Please send a screenshot of your WhatsApp username with your contact name for verification."
            )
            return reply, user_state

        # If user sends unexpected reply here
        reply, user_state = advance(
            sender,
            user_state,
            "start",
            "Hello! Are you a *student* or a *landlord*? Please reply with one."
        )
        return reply, user_state

    # WhatsApp verification for landlord (waiting for image screenshot)
    if step == "get_whatsapp_verification":
        # If user sent image, that is handled above.
        # Otherwise prompt user again
        reply = "Please send a screenshot of your WhatsApp username with your contact name for verification."
        return reply, user_state

    # After image approved (example flow)
    if step == "approve_manual":
        if msg in ["boys", "girls", "mixed"]:
            user_state["house_type"] = msg
            reply, user_state = advance(
                sender,
                user_state,
                "ask_cat_owner",
                "Do you have a *cat*? Please reply *yes* or *no*."
            )
            return reply, user_state
        else:
            return "Please reply with *boys*, *girls*, or *mixed*.", user_state

    # Ask if user has a cat
    if step == "ask_cat_owner":
        if msg in ["yes", "no"]:
            user_state["has_cat"] = msg
            reply, user_state = advance(
                sender,
                user_state,
                "ask_availability",
                "Do you have a vacancy? Reply *yes* or *no*."
            )
            return reply, user_state
        else:
            return "Do you have a cat? Please reply *yes* or *no*.", user_state

    # Ask if user has vacancy
    if step == "ask_availability":
        if msg == "no":
            reply, user_state = advance(
                sender,
                user_state,
                "end",
                "OK thanks. Whenever you have vacancies, don’t hesitate to say 'Hi!'"
            )
            return reply, user_state
        if msg == "yes":
            reply, user_state = advance(
                sender,
                user_state,
                "ask_room_type",
                "How many *boys* or *girls* do you need accommodation for in *single rooms*? (Enter number only)"
            )
            return reply, user_state
        else:
            return "Do you have a vacancy? Please reply *yes* or *no*.", user_state

    # Ask number of students for single rooms
    if step == "ask_room_type":
        if msg.isdigit():
            user_state["room_single"] = int(msg)
            reply, user_state = advance(
                sender,
                user_state,
                "confirm_single",
                "Please confirm your rent for a single room (e.g. 130)."
            )
            return reply, user_state
        else:
            return "Please enter the number of students needing single rooms (number only).", user_state

    # Confirm rent for single room
    if step == "confirm_single":
        try:
            rent_single = float(msg)
            user_state["rent_single"] = rent_single
            reply, user_state = advance(
                sender,
                user_state,
                "ask_2_sharing",
                "How many students need 2-sharing rooms? (Enter number only)"
            )
            return reply, user_state
        except ValueError:
            return "Please enter the rent as a number (e.g. 130).", user_state

    # Ask number for 2-sharing rooms
    if step == "ask_2_sharing":
        if msg.isdigit():
            user_state["room_2_sharing"] = int(msg)
            reply, user_state = advance(
                sender,
                user_state,
                "confirm_2_sharing",
                "Please confirm your rent for 2-sharing rooms (e.g. 80)."
            )
            return reply, user_state
        else:
            return "Please enter number of students needing 2-sharing rooms (number only).", user_state

    # Confirm rent for 2-sharing rooms
    if step == "confirm_2_sharing":
        try:
            rent_2_sharing = float(msg)
            user_state["rent_2_sharing"] = rent_2_sharing
            reply, user_state = advance(
                sender,
                user_state,
                "ask_3_sharing",
                "How many students need 3-sharing rooms? (Enter number only)"
            )
            return reply, user_state
        except ValueError:
            return "Please enter the rent as a number (e.g. 80).", user_state

    # Ask number for 3-sharing rooms
    if step == "ask_3_sharing":
        if msg.isdigit():
            user_state["room_3_sharing"] = int(msg)
            reply, user_state = advance(
                sender,
                user_state,
                "confirm_3_sharing",
                "Please confirm your rent for 3-sharing rooms (e.g. 60)."
            )
            return reply, user_state
        else:
            return "Please enter number of students needing 3-sharing rooms (number only).", user_state

    # Confirm rent for 3-sharing rooms
    if step == "confirm_3_sharing":
        try:
            rent_3_sharing = float(msg)
            user_state["rent_3_sharing"] = rent_3_sharing
            reply, user_state = advance(
                sender,
                user_state,
                "ask_student_age",
                "What age group are the students? (e.g. 18-22)"
            )
            return reply, user_state
        except ValueError:
            return "Please enter the rent as a number (e.g. 60).", user_state

    # Ask for student age group
    if step == "ask_student_age":
        user_state["student_age"] = message.strip()
        reply, user_state = advance(
            sender,
            user_state,
            "confirm_listing",
            "Thank you. Please confirm your listing by typing *confirm* or type *cancel* to abort."
        )
        return reply, user_state

    # Confirm listing submission
    if step == "confirm_listing":
        if msg == "confirm":
            user_state["step"] = "end"
            save_user_state(sender, user_state)
            return ("Thank you! Your listing will be published soon. Expect calls from students if you have vacancies."), user_state
        elif msg == "cancel":
            user_state["step"] = "end"
            save_user_state(sender, user_state)
            return ("Your listing was cancelled. Type 'Hi' to start over."), user_state
        else:
            return "Please type *confirm* to publish your listing or *cancel* to abort.", user_state

    # End state - user can restart conversation by typing 'hi'
    if step == "end":
        if msg == "hi":
            user_state["step"] = "start"
            save_user_state(sender, user_state)
            return ("Hello! Are you a *student* or a *landlord*? Please reply with one."), user_state
        else:
            return ("Thank you for contacting us. Type 'Hi' if you want to start again."), user_state

    # Default fallback for any unhandled step
    return ("Sorry, I did not understand that. Please try again."), user_state


class User:
    def __init__(self, phone_number):
        self.phone_number = phone_number
        self.language = "English"
        self.quote_data = {}
        self.booking_data = {}
        self.offer_data = {}

    def to_dict(self):
        return {
            "phone_number": self.phone_number,
            "language": self.language,
            "quote_data": self.quote_data,
            "booking_data": self.booking_data,
            "offer_data": self.offer_data
        }

    @classmethod
    def from_dict(cls, data):
        user = cls(data.get("phone_number"))
        user.language = data.get("language", "English")
        user.quote_data = data.get("quote_data", {})
        user.booking_data = data.get("booking_data", {})
        user.offer_data = data.get("offer_data", {})
        return user


def handle_approve_manual(prompt, user_data, phone_id):
    user = User.from_dict(user_data.get('user', {}))
    prompt = prompt.lower().strip()
    
    if prompt in ["boys", "girls", "mixed"]:
        user.booking_data["gender"] = prompt
        update_user_state(user_data['sender'], {
            "step": "collect_city",
            "user": user.to_dict()
        })
        send("Which city is your property located in?", user_data['sender'], phone_id)
        return {"step": "collect_city", "user": user.to_dict(), "sender": user_data['sender']}
    else:
        send("Please reply with 'boys', 'girls', or 'mixed'.", user_data['sender'], phone_id)
        return {"step": "approve_manual", "user": user.to_dict(), "sender": user_data['sender']}



# ==================== Flask Webhook Configuration ====================
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
            entry = data.get("entry", [])[0]
            changes = entry.get("changes", [])[0]
            value = changes.get("value", {})
            messages = value.get("messages", [])
            if not messages:
                logger.info("No valid messages in payload")
                return jsonify({"status": "ok", "message": "No messages"}), 200

            message = messages[0]
            sender = message.get("from")
            if not sender or not validate_whatsapp_number(sender):
                logger.error(f"Invalid sender ID: {sender}")
                return jsonify({"status": "error", "message": "Invalid sender"}), 400

            msg_type = message.get("type")
            msg = ""
            if msg_type == "text":
                msg = message["text"]["body"].strip().lower()
            elif msg_type == "image":
                msg = "image"  # Use placeholder for flow consistency

            name = None
            if 'contacts' in value and value['contacts']:
                contact = value['contacts'][0]
                name = contact.get('profile', {}).get('name')

            user_state = get_user_state(sender) or {}
            user_state.setdefault("user_id", sender)
            user_state.setdefault("user", User(sender).to_dict())
            step = user_state.get("step", "")

            # Step 0: On image upload – only once
            if msg_type == "image" and step != "approve_manual":
                send(
                    f"Thanks {name or 'there'}. Approval will be done manually for security reasons.\n\n"
                    "Now let’s collect house details.\n\n"
                    "Do you have accommodation for *boys*, *girls*, or *mixed*?",
                    sender, phone_id
                )
                user_state["step"] = "approve_manual"
                update_user_state(sender, user_state)
                return jsonify({"status": "ok"}), 200

            # --- STEP FLOW ---

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
                    reply = "Please enter the number of students needing single rooms (number only)."

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
                reply = "Thank you. Please confirm your listing by typing *confirm* or *cancel*."
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
                    reply = "Please type *confirm* to publish or *cancel* to abort."

            elif step == "end":
                if msg in ["hi", "hie", "hey"]:
                    reply = "Hello! Are you a *student* or a *landlord*?"
                    user_state["step"] = "start"
                    save_user_state(sender, user_state)
                else:
                    reply = "Thank you for contacting us. Type 'Hi' if you want to start again."

            else:
                reply = "Sorry, I did not understand that. Please try again."

            # Final response
            send(reply, sender, phone_id)
            update_user_state(sender, user_state)
            return jsonify({"status": "ok"}), 200

        except Exception as e:
            logger.exception("Unhandled error in webhook")
            return jsonify({"status": "error", "message": str(e)}), 500
    


            # Handle text messages
            if message.get("type") == "text" and "text" in message:
                text = message["text"]["body"].strip()
                logger.info(f"Processing text message from {sender}: '{text}'")
                reply, updated_state = message_handler(sender, text, user_state, value)
                save_user_state(sender, updated_state)
                send(reply, sender, value.get("metadata", {}).get("phone_number_id"))
                return jsonify({"reply": reply}), 200

            # Unhandled message types
            logger.info("Received unsupported message type")
            return jsonify({"status": "ignored", "message": "Unsupported message type"}), 200

        except Exception as e:
            logger.exception("Error handling webhook")
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
        "Authorization": f"Bearer {WA_TOKEN}",  # Use WA_TOKEN (consistent with webhook handler)
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
        logger.info(f"Message sent to {recipient}: {response.text}")
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
