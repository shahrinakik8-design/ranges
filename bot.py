import os
import time
import requests

# =========================
# CONFIG
# =========================

API_KEY = os.getenv("ZEBRASMS_API_KEY", "PUT_NEW_API_KEY_HERE")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "PUT_BOT_TOKEN_HERE")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "PUT_GROUP_CHAT_ID_HERE")

BASE_URL = "https://api.zebrasms.com/api/v1"

# কত সেকেন্ড পরপর liveaccess check করবে
CHECK_INTERVAL = 60

# চাইলে এখানে known range-এর country/service তথ্য রাখতে পারো
RANGE_INFO = {
    "2290163XXX": {
        "service": "Face-Book",
        "country": "Benin 🇧🇯"
    },

    "22501XXX": {
        "service": "Face-Book",
        "country": "Ivory Coast 🇨🇮"
    },

    "99277XXX": {
        "service": "Face-Book",
        "country": "Tajikistan 🇹🇯"
    },

    "26134XXX": {
        "service": "Instagram",
        "country": "Madagascar 🇲🇬"
    }
}


# =========================
# TELEGRAM
# =========================

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": text
    }

    try:
        r = requests.post(url, json=payload, timeout=20)
        data = r.json()

        if not data.get("ok"):
            print("Telegram error:", data)

            return False

        print("Message sent successfully")
        return True

    except Exception as e:
        print("Telegram request error:", e)
        return False


# =========================
# LIVE ACCESS
# =========================

def get_liveaccess():
    url = f"{BASE_URL}/publicapi/liveaccess"

    headers = {
        "MAuth": API_KEY
    }

    try:
        r = requests.get(
            url,
            headers=headers,
            timeout=20
        )

        data = r.json()

        if data.get("meta", {}).get("code") != 0:
            print("API error:", data)
            return []

        return data.get("data", {}).get("rows", [])

    except Exception as e:
        print("Liveaccess error:", e)
        return []


# =========================
# FORMAT MESSAGE
# =========================

def format_range_message(sender, range_name):

    info = RANGE_INFO.get(
        range_name,
        {
            "service": sender,
            "country": "Unknown 🌍"
        }
    )

    service = info["service"]
    country = info["country"]

    message = (
        "✅ New Active Range ✅\n"
        f"⚙️ Service: {service}\n"
        f"🌍 Country: {country}\n"
        f"📊 Range: {range_name}\n"
        "📩 Full SMS ⤵️⤵️"
    )

    return message


# =========================
# MAIN
# =========================

def main():

    print("Starting...")
    print("Live Range Monitor Started")

    # প্রথমবারের ranges শুধু memory-তে রাখবে,
    # যাতে bot চালু করলেই পুরোনো সব range spam না করে।
    initial_rows = get_liveaccess()

    known_ranges = set()

    for row in initial_rows:
        for range_name in row.get("ranges", []):
            known_ranges.add(
                (row.get("sender", ""), range_name)
            )

    print(f"Loaded {len(known_ranges)} existing ranges")

    while True:

        try:
            rows = get_liveaccess()

            current_ranges = set()

            for row in rows:

                sender = row.get("sender", "").strip()

                for range_name in row.get("ranges", []):

                    range_name = range_name.strip()

                    if not range_name:
                        continue

                    key = (sender.lower(), range_name)

                    current_ranges.add(key)

                    # নতুন range
                    if key not in known_ranges:

                        print(
                            f"New range detected: "
                            f"{sender} -> {range_name}"
                        )

                        message = format_range_message(
                            sender,
                            range_name
                        )

                        send_telegram(message)

            # নতুন/current ranges মনে রাখবে
            known_ranges.update(current_ranges)

            print(
                f"Checked liveaccess | "
                f"{len(current_ranges)} ranges"
            )

        except Exception as e:
            print("Main loop error:", e)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
