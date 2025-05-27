def generate_confirmation_message(student):
    """
    Generates a confirmation message for the landlord about a student.

    Args:
        student (dict): Student and landlord details.

    Returns:
        str: Message to send to the landlord.
    """
    name = student.get("name", "the student")
    gender = student.get("gender", "").lower()
    pronoun = "he" if gender == "male" else "she"
    part = student.get("part", "")
    room_type = student.get("room_type", "")
    budget = student.get("budget", "")
    city = student.get("city", "")
    house_name = student.get("house_name", "")
    move_in_date = student.get("move_in_date", "")
    multiple_houses = student.get("multiple_houses", False)
    landlord_name = student.get("landlord_name", "Landlord")

    intro = f"Hello {landlord_name} 👋, this is Talent from the student accommodation placement team."

    if multiple_houses:
        intro += (
            f" You have more than one house in {city}, so I need to confirm a student directly for one of your houses."
        )

    detail = (
        f"\n\nStudent: {name}"
        f"\nPart: {part}"
        f"\nRoom Type: {room_type}"
        f"\nGender: {gender.capitalize()}"
        f"\nCity: {city}"
        f"\nPreferred House: {house_name}"
        f"\nBudget: ${budget}"
        f"\nMove-in: {move_in_date}"
    )

    confirmation = (
        "\n\nIs there a room available at your place for this student?"
        "\nPlease reply with:"
        "\n✅ Yes - if there's a room"
        "\n❌ No - if you can't take this student"
        "\n🏠 Full - if the house is currently full"
    )

    return intro + detail + confirmation


def handle_landlord_reply(reply, student):
    """
    Handles the landlord's reply according to chat logic.

    Args:
        reply (str): The reply from the landlord.
        student (dict): Student and landlord details.

    Returns:
        str: The response to send to the landlord.
    """
    name = student.get("name", "the student")
    reply = reply.strip().lower()

    if reply in ["yes", "✅"]:
        return f"Great! I'll let {name} know that a room is available and proceed with confirmation. 🎉"
    elif reply in ["no", "❌"]:
        return (
            f"Noted. We’ll inform {name} that the place is not available."
            "\nLet us know if anything changes. 🙏"
        )
    elif reply in ["full", "🏠"]:
        return (
            "Okay, we’ll mark the house as full for now and not assign more students."
            "\nThanks for the update! 🏠"
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
    print(handle_landlord_reply("No", student_info))
    print(handle_landlord_reply("Full", student_info))
    print(handle_landlord_reply("maybe", student_info))
