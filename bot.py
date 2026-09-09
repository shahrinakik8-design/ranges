import os
import time
import requests

# =========================
# CONFIG
# =========================

API_KEY = os.getenv("ZEBRASMS_API_KEY", "")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BASE_URL = "https://api.zebrasms.com/api/v1"

CHECK_INTERVAL = 60
SEND_DELAY = 3


# =========================
# RANGE INFORMATION
# =========================

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

    while True:

        try:

            response = requests.post(
                url,
                json=payload,
                timeout=20
            )

            data = response.json()

            # Success
            if data.get("ok"):

                print("Message sent successfully")

                # Prevent Telegram rate limit
                time.sleep(SEND_DELAY)

                return True

            # Rate limit
            if data.get("error_code") == 429:

                retry_after = (
                    data.get("parameters", {})
                    .get("retry_after", 5)
                )

                print(
                    f"Telegram rate limited. "
                    f"Waiting {retry_after}s..."
                )

                time.sleep(retry_after)

                continue

            # Other Telegram error
            print("Telegram error:", data)

            return False

        except Exception as e:

            print("Telegram request error:", e)

            time.sleep(5)


# =========================
# ZEBRASMS LIVEACCESS
# =========================

def get_liveaccess():

    url = f"{BASE_URL}/publicapi/liveaccess"

    headers = {
        "MAuth": API_KEY
    }

    try:

        response = requests.get(
            url,
            headers=headers,
            timeout=20
        )

        data = response.json()

        # API success check
        if data.get("meta", {}).get("code") != 0:

            print("API error:", data)

            return []

        rows = data.get("data", {}).get("rows", [])

        return rows

    except Exception as e:

        print("Liveaccess error:", e)

        return []


# =========================
# FORMAT MESSAGE
# =========================

def format_range_message(sender, range_name):

    # Exact range information
    info = RANGE_INFO.get(
        range_name,
        {
            "service": sender.title(),
            "country": "Unknown 🌍"
        }
    )

    message = (
        "✅ New Active Range ✅\n"
        f"⚙️ Service: {info['service']}\n"
        f"🌍 Country: {info['country']}\n"
        f"📊 Range: {range_name}\n"
        "📩 SMS update available ⤵️⤵️"
    )

    return message


# =========================
# MAIN
# =========================

def main():

    print("Starting...")
    print("Live Range Monitor Started")

    # Check configuration
    if not API_KEY:

        print("ERROR: ZEBRASMS_API_KEY is missing")

        return

    if not BOT_TOKEN:

        print("ERROR: TELEGRAM_BOT_TOKEN is missing")

        return

    if not CHAT_ID:

        print("ERROR: TELEGRAM_CHAT_ID is missing")

        return

    # =========================
    # INITIAL LOAD
    # =========================

    initial_rows = get_liveaccess()

    known_ranges = set()

    for row in initial_rows:

        sender = str(
            row.get("sender", "")
        ).strip().lower()

        ranges = row.get("ranges", [])

        for range_name in ranges:

            range_name = str(
                range_name
            ).strip().upper()

            if not range_name:
                continue

            key = (
                sender,
                range_name
            )

            known_ranges.add(key)

    print(
        f"Loaded {len(known_ranges)} existing ranges"
    )

    # =========================
    # MONITOR LOOP
    # =========================

    while True:

        try:

            rows = get_liveaccess()

            current_ranges = set()

            for row in rows:

                sender = str(
                    row.get("sender", "")
                ).strip().lower()

                ranges = row.get("ranges", [])

                for range_name in ranges:

                    range_name = str(
                        range_name
                    ).strip().upper()

                    if not range_name:
                        continue

                    key = (
                        sender,
                        range_name
                    )

                    current_ranges.add(key)

                    # New range
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

            # Remember current ranges
            known_ranges.update(current_ranges)

            print(
                f"Checked liveaccess | "
                f"{len(current_ranges)} ranges"
            )

        except Exception as e:

            print(
                "Main loop error:",
                e
            )

        print(
            f"Next check in {CHECK_INTERVAL} seconds..."
        )

        time.sleep(CHECK_INTERVAL)


# =========================
# START
# =========================

if __name__ == "__main__":
    main()
