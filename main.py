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
            msg = message.get("text", {}).get("body", "").strip().lower() if msg_type == "text" else ""

            user_state = get_user_state(sender) or {}
            if "user" not in user_state:
                user_state["user"] = {"name": name}
                user_state["user_id"] = sender

            step = user_state.get("step")
            if not step:
                user_state["step"] = "start"
                step = "start"

            if msg in ["hi", "hie", "hey"]:
                reply = "Hello! Are you a *student* or a *landlord*? Please reply with one."
                user_state["step"] = "start"
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

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

            if msg_type == "image":
                if step == "awaiting_image":
                    reply = (
                        f"Thanks {name} for the image.\n\n"
                        "Now let’s collect house details.\n\n"
                        "Do you have accommodation for *boys*, *girls*, or *mixed*?"
                    )
                else:
                    reply = f"Thanks {name}. Do you have accommodation for *boys*, *girls*, or *mixed*?"
                user_state["step"] = "manual"
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

            elif step == "manual":
                try:
                    if msg in ["boys", "girls", "mixed"]:
                        user_state["house_type"] = msg
                        user_state["step"] = "ask_cat_owner"
                        reply = "Do you have a cat? Reply *yes* or *no*."
                    else:
                        reply = "Please reply with *boys*, *girls*, or *mixed*."
                except Exception as e:
                    logger.exception("Error in 'manual' step")
                    reply = "Oops! Something went wrong while processing your response. Please try again."
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

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

            elif step == "ask_room_type":
                if msg.isdigit():
                    user_state["room_single"] = int(msg)
                    user_state["step"] = "confirm_single"
                    reply = "Please confirm your rent for a single room (e.g. 130)."
                else:
                    reply = "Please enter the number of students needing single rooms (number only)."
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

            elif step == "confirm_single":
                try:
                    user_state["rent_single"] = float(msg)
                    user_state["step"] = "ask_2_sharing"
                    reply = "How many students need 2-sharing rooms? (Enter number only)"
                except ValueError:
                    reply = "Please enter the rent as a number (e.g. 130)."
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

            elif step == "ask_2_sharing":
                if msg.isdigit():
                    user_state["room_2_sharing"] = int(msg)
                    user_state["step"] = "confirm_2_sharing"
                    reply = "Please confirm your rent for 2-sharing rooms (e.g. 80)."
                else:
                    reply = "Please enter number of students needing 2-sharing rooms (number only)."
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

            elif step == "confirm_2_sharing":
                try:
                    user_state["rent_2_sharing"] = float(msg)
                    user_state["step"] = "ask_3_sharing"
                    reply = "How many students need 3-sharing rooms? (Enter number only)"
                except ValueError:
                    reply = "Please enter the rent as a number (e.g. 80)."
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

            elif step == "ask_3_sharing":
                if msg.isdigit():
                    user_state["room_3_sharing"] = int(msg)
                    user_state["step"] = "confirm_3_sharing"
                    reply = "Please confirm your rent for 3-sharing rooms (e.g. 60)."
                else:
                    reply = "Please enter number of students needing 3-sharing rooms (number only)."
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

            elif step == "confirm_3_sharing":
                try:
                    user_state["rent_3_sharing"] = float(msg)
                    user_state["step"] = "ask_student_age"
                    reply = "What age group are the students? (e.g. 18-22)"
                except ValueError:
                    reply = "Please enter the rent as a number (e.g. 60)."
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

            elif step == "ask_student_age":
                user_state["student_age"] = msg
                user_state["step"] = "confirm_listing"
                reply = "Thank you. Please confirm your listing by typing *confirm* or type *cancel* to abort."
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

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
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

            elif step == "end":
                if msg in ["hi", "hie", "hey"]:
                    reply = "Welcome back! Are you a *student* or a *landlord*?"
                    user_state["step"] = "start"
                else:
                    reply = "Thank you for contacting us. Type 'Hi' to start again."
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

            else:
                reply = "Sorry, I did not understand that. Please try again."
                update_user_state(sender, user_state)
                send(reply, sender, phone_id)
                return jsonify({"status": "ok"}), 200

        except Exception as e:
            logger.exception("Unhandled error in webhook")
            return jsonify({"status": "error", "message": str(e)}), 500
