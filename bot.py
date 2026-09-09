import os
import time
import json
import hashlib
import requests


# =========================================================
# CONFIG
# =========================================================

API_KEY = os.getenv("ZEBRASMS_API_KEY", "")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BASE_URL = "https://api.zebrasms.com/api/v1"

CHECK_INTERVAL = 60
SEND_DELAY = 3


# =========================================================
# RANGE INFORMATION
# =========================================================

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


# =========================================================
# HTTP SESSION
# =========================================================

session = requests.Session()

session.headers.update({
    "MAuth": API_KEY,
    "Accept": "application/json"
})


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(text):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": text
    }

    while True:

        try:

            response = session.post(
                url,
                json=payload,
                timeout=20
            )

            try:
                data = response.json()
            except Exception:
                data = {}

            # -------------------------
            # SUCCESS
            # -------------------------

            if data.get("ok"):

                print("Telegram message sent")

                time.sleep(SEND_DELAY)

                return True

            # -------------------------
            # RATE LIMIT
            # -------------------------

            if data.get("error_code") == 429:

                retry_after = (
                    data.get("parameters", {})
                    .get("retry_after", 5)
                )

                print(
                    f"Telegram rate limit. "
                    f"Waiting {retry_after}s..."
                )

                time.sleep(retry_after)

                continue

            # -------------------------
            # OTHER ERROR
            # -------------------------

            print(
                "Telegram error:",
                data.get("description", data)
            )

            return False

        except requests.RequestException as e:

            print(
                "Telegram connection error:",
                e
            )

            time.sleep(5)

        except Exception as e:

            print(
                "Telegram error:",
                e
            )

            time.sleep(5)


# =========================================================
# ZEBRASMS API
# =========================================================

def api_get(endpoint):

    url = f"{BASE_URL}{endpoint}"

    try:

        response = session.get(
            url,
            timeout=20
        )

        if response.status_code != 200:

            print(
                f"API HTTP error: "
                f"{response.status_code}"
            )

            return None

        try:
            data = response.json()
        except Exception:

            print("API returned invalid JSON")

            return None

        meta = data.get("meta", {})

        if meta.get("code") != 0:

            print(
                "ZebraSMS API error:",
                meta
            )

            return None

        return data

    except requests.RequestException as e:

        print(
            f"API connection error "
            f"({endpoint}):",
            e
        )

        return None

    except Exception as e:

        print(
            f"API error ({endpoint}):",
            e
        )

        return None


# =========================================================
# LIVEACCESS
# =========================================================

def get_liveaccess():

    data = api_get(
        "/publicapi/liveaccess"
    )

    if not data:
        return []

    api_data = data.get("data", {})

    if isinstance(api_data, dict):

        rows = api_data.get(
            "rows",
            []
        )

        if isinstance(rows, list):
            return rows

    if isinstance(api_data, list):
        return api_data

    return []


# =========================================================
# LIVE RANGE EXTRACTION
# =========================================================

def extract_live_ranges(rows):

    result = set()

    for row in rows:

        if not isinstance(row, dict):
            continue

        sender = str(
            row.get("sender", "")
        ).strip().lower()

        ranges = row.get(
            "ranges",
            []
        )

        if isinstance(ranges, str):
            ranges = [ranges]

        if not isinstance(ranges, list):
            continue

        for range_name in ranges:

            range_name = str(
                range_name
            ).strip().upper()

            if not range_name:
                continue

            result.add(
                (
                    sender,
                    range_name
                )
            )

    return result


# =========================================================
# RANGE INFO
# =========================================================

def get_range_info(sender, range_name):

    # Exact match
    if range_name in RANGE_INFO:

        return RANGE_INFO[range_name]

    # Unknown range
    return {
        "service": sender.title()
        if sender
        else "Unknown",

        "country": "Unknown 🌍"
    }


# =========================================================
# NEW RANGE MESSAGE
# =========================================================

def format_range_message(
    sender,
    range_name
):

    info = get_range_info(
        sender,
        range_name
    )

    return (
        "✅ New Active Range ✅\n"
        f"⚙️ Service: {info['service']}\n"
        f"🌍 Country: {info['country']}\n"
        f"📊 Range: {range_name}\n"
        "📩 SMS update available"
    )


# =========================================================
# GETUPDATE
# =========================================================

def get_updates():

    data = api_get(
        "/publicapi/getupdate"
    )

    if not data:
        return None

    return data


# =========================================================
# SAFE UPDATE METADATA
# =========================================================

SAFE_FIELDS = {
    "id",
    "update_id",
    "message_id",
    "range",
    "sender",
    "service",
    "country",
    "created_at",
    "timestamp",
    "time",
    "date"
}


def safe_value(value):

    if value is None:
        return ""

    if isinstance(value, (str, int, float, bool)):
        return str(value)

    return ""


def extract_update_records(obj):

    records = []

    def walk(value):

        if isinstance(value, list):

            for item in value:
                walk(item)

            return

        if not isinstance(value, dict):
            return

        safe = {}

        for key, val in value.items():

            key_lower = str(
                key
            ).lower()

            if key_lower in SAFE_FIELDS:

                cleaned = safe_value(val)

                if cleaned:
                    safe[key_lower] = cleaned

        # If this object contains useful metadata,
        # treat it as an update record.
        if safe:

            records.append(safe)

        # Continue through nested objects.
        for key, val in value.items():

            if isinstance(
                val,
                (dict, list)
            ):

                walk(val)

    walk(obj)

    return records


# =========================================================
# UPDATE KEY
# =========================================================

def make_update_key(record):

    # Prefer a unique API ID.
    for field in (
        "update_id",
        "message_id",
        "id"
    ):

        value = record.get(field)

        if value:

            return (
                field,
                value
            )

    # Otherwise use available safe metadata.
    safe_data = {
        key: record[key]
        for key in sorted(record)
    }

    raw = json.dumps(
        safe_data,
        sort_keys=True,
        ensure_ascii=False
    )

    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()

    return (
        "hash",
        digest
    )


# =========================================================
# UPDATE MESSAGE
# =========================================================

def format_update_message(record):

    sender = record.get(
        "sender",
        ""
    ).strip().lower()

    range_name = record.get(
        "range",
        ""
    ).strip().upper()

    service = record.get(
        "service",
        ""
    ).strip()

    country = record.get(
        "country",
        ""
    ).strip()

    # If API does not directly provide service/country,
    # use our local range mapping.
    if range_name:

        info = get_range_info(
            sender,
            range_name
        )

        if not service:
            service = info["service"]

        if not country:
            country = info["country"]

    if not service:
        service = (
            sender.title()
            if sender
            else "Unknown"
        )

    if not country:
        country = "Unknown 🌍"

    if range_name:

        return (
            "✅ New SMS Update ✅\n"
            f"⚙️ Service: {service}\n"
            f"🌍 Country: {country}\n"
            f"📊 Range: {range_name}\n"
            "📩 New SMS received"
        )

    return (
        "✅ New SMS Update ✅\n"
        f"⚙️ Service: {service}\n"
        f"🌍 Country: {country}\n"
        "📩 New SMS received"
    )


# =========================================================
# INITIAL UPDATE LOAD
# =========================================================

def load_initial_updates():

    data = get_updates()

    if data is None:

        print(
            "Initial getupdate check failed"
        )

        return set()

    records = extract_update_records(
        data
    )

    known = set()

    for record in records:

        key = make_update_key(
            record
        )

        known.add(key)

    print(
        f"Loaded {len(known)} existing SMS updates"
    )

    return known


# =========================================================
# MAIN
# =========================================================

def main():

    print("Starting...")
    print("Live Range + SMS Update Monitor Started")

    # -----------------------------------------------------
    # CONFIG CHECK
    # -----------------------------------------------------

    if not API_KEY:

        print(
            "ERROR: ZEBRASMS_API_KEY is missing"
        )

        return

    if not BOT_TOKEN:

        print(
            "ERROR: TELEGRAM_BOT_TOKEN is missing"
        )

        return

    if not CHAT_ID:

        print(
            "ERROR: TELEGRAM_CHAT_ID is missing"
        )

        return

    print("Configuration OK")

    # -----------------------------------------------------
    # INITIAL LIVE RANGES
    # -----------------------------------------------------

    initial_rows = get_liveaccess()

    known_ranges = extract_live_ranges(
        initial_rows
    )

    print(
        f"Loaded {len(known_ranges)} existing ranges"
    )

    # -----------------------------------------------------
    # INITIAL SMS UPDATES
    # -----------------------------------------------------

    known_updates = load_initial_updates()

    print(
        "Initial data loaded"
    )

    # -----------------------------------------------------
    # MONITOR
    # -----------------------------------------------------

    while True:

        try:

            # =============================================
            # 1. LIVEACCESS
            # =============================================

            rows = get_liveaccess()

            current_ranges = extract_live_ranges(
                rows
            )

            new_ranges = (
                current_ranges
                - known_ranges
            )

            for sender, range_name in sorted(
                new_ranges
            ):

                print(
                    f"New range detected: "
                    f"{sender} -> {range_name}"
                )

                message = format_range_message(
                    sender,
                    range_name
                )

                send_telegram(
                    message
                )

            known_ranges.update(
                current_ranges
            )

            print(
                f"Checked liveaccess | "
                f"{len(current_ranges)} ranges | "
                f"{len(new_ranges)} new"
            )

            # =============================================
            # 2. GETUPDATE
            # =============================================

            update_data = get_updates()

            if update_data is not None:

                update_records = extract_update_records(
                    update_data
                )

                current_updates = set()

                new_update_records = []

                for record in update_records:

                    key = make_update_key(
                        record
                    )

                    current_updates.add(
                        key
                    )

                    if key not in known_updates:

                        new_update_records.append(
                            record
                        )

                # -----------------------------------------
                # SEND NEW UPDATE NOTIFICATIONS
                # -----------------------------------------

                for record in new_update_records:

                    print(
                        "New SMS update detected"
                    )

                    message = format_update_message(
                        record
                    )

                    send_telegram(
                        message
                    )

                known_updates.update(
                    current_updates
                )

                print(
                    f"Checked getupdate | "
                    f"{len(current_updates)} updates | "
                    f"{len(new_update_records)} new"
                )

            else:

                print(
                    "Checked getupdate | API unavailable"
                )

        except Exception as e:

            print(
                "Main loop error:",
                e
            )

        print(
            f"Next check in "
            f"{CHECK_INTERVAL} seconds..."
        )

        time.sleep(
            CHECK_INTERVAL
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    main()
