import requests
import json
import os

# Set these as environment variables in your deployment environment
UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")

HEADERS = {
    "Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}",
    "Content-Type": "application/json"
}

def get_user_state(user_id):
    """
    Retrieve the user's state from Redis.
    """
    url = f"{UPSTASH_REDIS_REST_URL}/get/{user_id}"
    try:
        response = requests.get(url, headers=HEADERS)
        if response.status_code == 200:
            data = response.json()
            if "result" in data and data["result"]:
                return json.loads(data["result"])
        return {}
    except Exception as e:
        print(f"[Redis] Error getting user state: {e}")
        return {}

def save_user_state(user_id, state):
    """
    Save the user's state in Redis.
    """
    url = f"{UPSTASH_REDIS_REST_URL}/set/{user_id}"
    try:
        payload = json.dumps(state)
        response = requests.post(url, headers=HEADERS, data=payload)
        return response.status_code == 200
    except Exception as e:
        print(f"[Redis] Error saving user state: {e}")
        return False

def update_user_state(user_id, updates):
    """
    Update specific fields in the user's state and save it.
    """
    current_state = get_user_state(user_id)
    current_state.update(updates)
    return save_user_state(user_id, current_state)
