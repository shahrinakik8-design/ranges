import os
import time
import json
import hashlib
import requests

BASE_URL = "https://api.zebrasms.com/api/v1"

CHECK_INTERVAL = 30
SEND_DELAY = 2

API_KEY = os.getenv("ZEBRASMS_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

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


session = requests.Session()

session.headers.update({
    "MAuth": API_KEY or "",
    "Accept": "*/*",
    "User-Agent": "Mozilla/5.0"
})


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=20
        )

        if response.status_code == 429:
            try:
                retry_after = response.json()["parameters"]["retry_after"]
                print(f"Telegram rate limited. Waiting {retry_after}s...")
                time.sleep(retry_after)
                return send_telegram(text)
            except Exception:
                pass

        if response.status_code != 200:
            print(
                "Telegram error:",
                response.status_code,
                response.text[:300]
            )
            return False

        time.sleep(SEND_DELAY)
        return True

    except Exception as e:
        print("Telegram exception:", e)
        return False


def get_liveaccess():
    try:
        response = session.get(
            f"{BASE_URL}/publicapi/liveaccess",
            timeout=20
        )

        if response.status_code != 200:
            print(
                "liveaccess HTTP error:",
                response.status_code
            )
            return []

        data = response.json()

        rows = data.get("data", {}).get("rows", [])

        if not isinstance(rows, list):
            return []

        return rows

    except Exception as e:
        print("liveaccess error:", e)
        return []


def normalize_range_info(sender, range_value):
    info = RANGE_INFO.get(range_value)

    if info:
        return info["service"], info["country"]

    sender_lower = str(sender or "").lower()

    if "facebook" in sender_lower:
        service = "Face-Book"
    elif "instagram" in sender_lower:
        service = "Instagram"
    else:
        service = str(sender or "Unknown")

    return service, "Unknown"


def extract_live_ranges(rows):
    result = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        sender = row.get("sender", "")

        ranges = row.get("ranges", [])

        if isinstance(ranges, str):
            ranges = [ranges]

        if not isinstance(ranges, list):
            continue

        for range_value in ranges:
            range_value = str(range_value)

            result.append({
                "sender": sender,
                "range": range_value
            })

    return result


def format_range_message(record):
    sender = record.get("sender", "")
    range_value = record.get("range", "")

    service, country = normalize_range_info(
        sender,
        range_value
    )

    return (
        "🟢 New Active Range\n\n"
        f"⚙️ Service: {service}\n"
        f"🌍 Country: {country}\n"
        f"📊 Range: {range_value}\n"
        "\n"
        "📡 Range is currently active"
    )


def decode_console_response(response):
    """
    /console may return CBOR directly or hexadecimal CBOR.
    JSON fallback is also supported.
    """

    # 1. Try normal JSON
    try:
        return response.json()
    except Exception:
        pass

    # 2. Try CBOR
    try:
        import cbor2

        raw = response.content

        try:
            return cbor2.loads(raw)
        except Exception:
            pass

        # 3. Try hex encoded CBOR
        text = response.text.strip()

        try:
            decoded_bytes = bytes.fromhex(text)
            return cbor2.loads(decoded_bytes)
        except Exception:
            pass

    except Exception as e:
        print("CBOR decode error:", e)

    return None


def get_console():
    try:
        response = session.get(
            f"{BASE_URL}/console",
            timeout=20
        )

        if response.status_code != 200:
            print(
                "console HTTP error:",
                response.status_code
            )
            return None

        return decode_console_response(response)

    except Exception as e:
        print("console error:", e)
        return None


def find_rows(obj):
    """
    Recursively find a 'rows' list anywhere
    inside the console response.
    """

    if isinstance(obj, dict):

        if "rows" in obj and isinstance(obj["rows"], list):
            return obj["rows"]

        for value in obj.values():
            result = find_rows(value)

            if result is not None:
                return result

    elif isinstance(obj, list):

        for item in obj:
            result = find_rows(item)

            if result is not None:
                return result

    return None


def extract_console_records(console_data):
    rows = find_rows(console_data)

    if not rows:
        return []

    records = []

    for row in rows:

        if not isinstance(row, dict):
            continue

        # Only safe metadata.
        # IMPORTANT:
        # message / sms / otp / code / number are never read.
        record = {
            "idx": row.get("idx"),
            "eat_ms": row.get("eat_ms"),
            "range": row.get("range"),
            "sender": row.get("sender"),
            "country": row.get("country"),
            "operator": row.get("operator")
        }

        if not any(
            value is not None
            for value in record.values()
        ):
            continue

        records.append(record)

    return records


def record_key(record):
    """
    Prefer idx because it is the unique console record ID.
    """

    if record.get("idx") is not None:
        return f"idx:{record['idx']}"

    safe_data = {
        "eat_ms": record.get("eat_ms"),
        "range": record.get("range"),
        "sender": record.get("sender"),
        "country": record.get("country"),
        "operator": record.get("operator")
    }

    raw = json.dumps(
        safe_data,
        sort_keys=True,
        ensure_ascii=False
    )

    return "hash:" + hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def format_console_message(record):

    service = record.get("sender") or "Unknown"
    country = record.get("country") or "Unknown"
    range_value = record.get("range") or "Unknown"
    operator = record.get("operator") or "Unknown"

    return (
        "🔔 New SMS Update\n\n"
        f"⚙️ Service: {service}\n"
        f"🌍 Country: {country}\n"
        f"📊 Range: {range_value}\n"
        f"📡 Operator: {operator}\n"
        "\n"
        "📩 SMS received\n"
        "🔒 Message: ******** MASKED ********"
    )


def load_initial_ranges():
    rows = get_liveaccess()
    records = extract_live_ranges(rows)

    known = set()

    for record in records:
        key = (
            record["sender"],
            record["range"]
        )
        known.add(key)

    print(
        f"Loaded {len(known)} existing ranges"
    )

    return known


def load_initial_console():
    data = get_console()

    if data is None:
        print("Initial console data unavailable")
        return set()

    records = extract_console_records(data)

    known = set()

    for record in records:
        known.add(record_key(record))

    print(
        f"Loaded {len(known)} existing SMS updates"
    )

    return known


def check_liveaccess(known_ranges):

    rows = get_liveaccess()

    records = extract_live_ranges(rows)

    new_count = 0

    for record in records:

        key = (
            record["sender"],
            record["range"]
        )

        if key in known_ranges:
            continue

        known_ranges.add(key)

        message = format_range_message(record)

        if send_telegram(message):
            new_count += 1

    print(
        f"Checked liveaccess | "
        f"{len(records)} ranges | "
        f"{new_count} new"
    )


def check_console(known_console):

    data = get_console()

    if data is None:
        print("Checked console | unavailable")
        return

    records = extract_console_records(data)

    new_count = 0

    for record in records:

        key = record_key(record)

        if key in known_console:
            continue

        known_console.add(key)

        message = format_console_message(record)

        if send_telegram(message):
            new_count += 1

    # Prevent unlimited memory growth
    if len(known_console) > 5000:
        known_console.clear()

        for record in records:
            known_console.add(record_key(record))

    print(
        f"Checked console | "
        f"{len(records)} updates | "
        f"{new_count} new"
    )


def main():

    print("Starting...")
    print("Live Range + SMS Update Monitor Started")

    if not API_KEY:
        print("ERROR: ZEBRASMS_API_KEY is missing")
        return

    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN is missing")
        return

    if not TELEGRAM_CHAT_ID:
        print("ERROR: TELEGRAM_CHAT_ID is missing")
        return

    print("Configuration OK")

    known_ranges = load_initial_ranges()
    known_console = load_initial_console()

    print("Initial data loaded")

    while True:

        try:
            check_liveaccess(known_ranges)

            check_console(known_console)

            print(
                f"Next check in "
                f"{CHECK_INTERVAL} seconds..."
            )

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("Bot stopped")
            break

        except Exception as e:
            print("Main loop error:", e)
            time.sleep(10)


if __name__ == "__main__":
    main()
