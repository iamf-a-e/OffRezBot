
def generate_confirmation_message(student):
    name = student["name"]
    gender = student["gender"].lower()
    pronoun = "he" if gender == "male" else "she"
    part = student["part"]
    room_type = student["room_type"]
    budget = student["budget"]
    city = student["city"]
    house_name = student["house_name"]
    move_in_date = student["move_in_date"]
    multiple_houses = student["multiple_houses"]

    intro = f"Hello {student['landlord_name']} 👋, this is Talent from the student accommodation placement team."

    if multiple_houses:
        intro += f" You have more than one house in {city}, so I need to confirm a student directly for one of your houses."

    detail = (
        f"

Student: {name}
"
        f"Part: {part}
"
        f"Room Type: {room_type}
"
        f"Gender: {gender}
"
        f"City: {city}
"
        f"Preferred House: {house_name}
"
        f"Budget: ${budget}
"
        f"Move-in: {move_in_date}"
    )

    confirmation = (
        "

Is there a room available at your place for this student?"
        "
Please reply with:
"
        "✅ Yes - if there's a room
"
        "❌ No - if you can't take this student
"
        "🏠 Full - if the house is currently full"
    )

    return intro + detail + confirmation


def handle_landlord_reply(reply, student):
    reply = reply.strip().lower()
    if reply in ["yes", "✅"]:
        return f"Great! I'll let {student['name']} know that a room is available and proceed with confirmation. 🎉"
    elif reply in ["no", "❌"]:
        return (
            f"Noted. We’ll inform {student['name']} that the place is not available."
            "
Let us know if anything changes. 🙏"
        )
    elif reply in ["full", "🏠"]:
        return (
            f"Okay, we’ll mark the house as full for now and not assign more students."
            "
Thanks for the update! 🏠"
        )
    else:
        return "Sorry, I didn’t understand your response. Please reply with ✅ Yes, ❌ No, or 🏠 Full."


# Example test
if __name__ == "__main__":
    student_info = {
        "name": "Sarah Mahombe",
        "gender": "female",
        "part": "1.1",
        "room_type": "2-sharing",
        "budget": 120,
        "city": "Harare",
        "house_name": "Rosewood Villa",
        "move_in_date": "June 1",
        "multiple_houses": True,
        "landlord_name": "Mr. Nyasha"
    }

    print(generate_confirmation_message(student_info))
    print()
    print(handle_landlord_reply("Yes", student_info))
