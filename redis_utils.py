import os
import json
import requests

# Load environment variables
REDIS_URL = os.environ.get("UPSTASH_REDIS_REST_URL")
REDIS_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN")

HEADERS = {
    "Authorization": f"Bearer {REDIS_TOKEN}",
    "Content-Type": "application/json"
}


# ---------- USER STATE MANAGEMENT ----------

def get_user_state(user_id):
    try:
        url = f"{REDIS_URL}/get/user:{user_id}"
        response = requests.get(url, headers=HEADERS)
        if response.status_code == 200 and response.text.startswith("OK"):
            data = response.text.replace("OK", "").strip()
            return json.loads(data)
        return None
    except Exception as e:
        print(f"Error getting user state for {user_id}: {e}")
        return None


def update_user_state(user_id, state):
    try:
        url = f"{REDIS_URL}/set/user:{user_id}"
        payload = {
            "_value": json.dumps(state),
            "_ttl": 3600 * 24 * 2  # 2 days
        }
        response = requests.post(url, headers=HEADERS, json=payload)
        return response.status_code == 200
    except Exception as e:
        print(f"Error updating user state for {user_id}: {e}")
        return False


def save_user_state(user_id, state):
    """Alias to update_user_state for clarity."""
    return update_user_state(user_id, state)


# ---------- DUPLICATE MESSAGE DETECTION ----------

def is_duplicate_message(user_id, message_id):
    try:
        key = f"dedup:{user_id}"
        get_url = f"{REDIS_URL}/get/{key}"
        get_response = requests.get(get_url, headers=HEADERS)

        # Extract stored list or fallback
        if get_response.status_code == 200 and get_response.text.startswith("OK"):
            old = json.loads(get_response.text.replace("OK", "").strip())
            if message_id in old:
                return True
        else:
            old = []

        # Update message ID list (keep last 5 only)
        old.append(message_id)
        old = old[-5:]

        # Save back to Redis
        payload = {"_value": json.dumps(old), "_ttl": 3600}
        put_url = f"{REDIS_URL}/set/{key}"
        requests.post(put_url, headers=HEADERS, json=payload)
        return False

    except Exception as e:
        print(f"Error in duplicate check for {user_id}: {e}")
        return False
