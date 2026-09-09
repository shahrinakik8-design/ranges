import os
import time
import json
import html
import hashlib
import requests

try:
    import cbor2
except ImportError:
    cbor2 = None


# =========================================================
# CONFIG
# =========================================================

BASE_URL = "https://api.zebrasms.com/api/v1"

API_KEY = (os.getenv("ZEBRASMS_API_KEY") or "").strip()
CONSOLE_TOKEN = (os.getenv("ZEBRASMS_CONSOLE_TOKEN") or "").strip()

TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

try:
    CHECK_INTERVAL = int(
        (os.getenv("CHECK_INTERVAL") or "5").strip()
    )
except ValueError:
    CHECK_INTERVAL = 5


# =========================================================
# SESSIONS
# =========================================================

api_session = requests.Session()

api_session.headers.update({
    "MAuth": API_KEY,
    "Accept": "*/*",
    "User-Agent": "Mozilla/5.0"
})


console_session = requests.Session()

console_session.headers.update({
    "MAuth": CONSOLE_TOKEN,
    "Accept": "*/*",
    "Origin": "https://zebrasms.com",
    "Referer": "https://zebrasms.com/",
    "User-Agent": "Mozilla/5.0"
})


# =========================================================
# TELEGRAM HELPERS
# =========================================================

def tg_escape(value):
    """
    Escape text safely for Telegram HTML parse mode.
    """
    if value is None:
        return ""

    return html.escape(str(value), quote=False)


def send_telegram(text):
    """
    Send formatted HTML message to Telegram.
    Handles Telegram 429 rate limits.
    """

    if not TELEGRAM_BOT_TOKEN:
        print("Telegram bot token missing")
        return False

    if not TELEGRAM_CHAT_ID:
        print("Telegram chat ID missing")
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=20
        )

        if response.status_code == 429:

            try:
                retry_after = (
                    response
                    .json()
                    .get("parameters", {})
                    .get("retry_after", 5)
                )
            except Exception:
                retry_after = 5

            print(
                f"Telegram rate limited. "
                f"Waiting {retry_after}s..."
            )

            time.sleep(int(retry_after))

            response = requests.post(
                url,
                json=payload,
                timeout=20
            )

        if response.ok:
            return True

        print(
            "Telegram error:",
            response.status_code,
            response.text[:500]
        )

    except Exception as e:
        print("Telegram exception:", e)

    return False


# =========================================================
# LIVEACCESS
# =========================================================

def get_liveaccess():

    url = f"{BASE_URL}/publicapi/liveaccess"

    try:

        response = api_session.get(
            url,
            timeout=20
        )

        if not response.ok:

            print(
                "liveaccess HTTP error:",
                response.status_code,
                response.text[:300]
            )

            return None

        return response.json()

    except Exception as e:

        print(
            "liveaccess error:",
            e
        )

        return None


def extract_live_entries(data):

    entries = []

    def walk(obj):

        if isinstance(obj, dict):

            if "range" in obj:

                range_value = obj.get("range")

                if range_value is not None:

                    entries.append({
                        "range": str(range_value),
                        "sender": (
                            obj.get("sender")
                            or obj.get("service")
                            or obj.get("brand")
                            or ""
                        ),
                        "country": (
                            obj.get("country")
                            or obj.get("country_name")
                            or ""
                        ),
                        "operator": (
                            obj.get("operator")
                            or obj.get("network")
                            or ""
                        )
                    })

            for value in obj.values():
                walk(value)

        elif isinstance(obj, list):

            for item in obj:
                walk(item)

        elif isinstance(obj, str):

            value = obj.strip()

            if value and (
                value.startswith("+")
                or value.isdigit()
            ):

                entries.append({
                    "range": value,
                    "sender": "",
                    "country": "",
                    "operator": ""
                })

    walk(data)

    unique = {}

    for item in entries:

        rng = item.get("range", "").strip()

        if not rng:
            continue

        if rng not in unique:
            unique[rng] = item

    return list(unique.values())


def format_live_range_message(entry):

    rng = tg_escape(
        entry.get("range") or "Unknown"
    )

    sender = tg_escape(
        entry.get("sender") or "Unknown"
    )

    country = tg_escape(
        entry.get("country") or "Unknown"
    )

    operator = tg_escape(
        entry.get("operator") or "Unknown"
    )
    message = tg_escape(
        entry.get("message") or "Unknown"
    )
    return (
        "🟢 <b>NEW LIVE RANGE</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"📋 <b>Range</b>\n"
        f"<code>{rng}</code>\n\n"

        f"📨 <b>Service:</b> {sender}\n"
        f"🌍 <b>Country:</b> {country}\n"
        f"📡 <b>Operator:</b> {operator}\n\n"
         f"📡 <b>message:</b> {operator}\n\n"

        "✨ <i>New live range detected</i>"
    )


# =========================================================
# CONSOLE DECODER
# =========================================================

def decode_console_response(response):

    raw = response.content

    if not raw:
        return None

    # -----------------------------------------
    # Normal JSON
    # -----------------------------------------

    try:
        return response.json()

    except Exception:
        pass


    # -----------------------------------------
    # UTF-8 JSON
    # -----------------------------------------

    try:

        text = raw.decode("utf-8").strip()

        if text:
            return json.loads(text)

    except Exception:
        pass


    # -----------------------------------------
    # Raw CBOR
    # -----------------------------------------

    if cbor2 is not None:

        try:
            return cbor2.loads(raw)

        except Exception:
            pass


    # -----------------------------------------
    # Hex encoded CBOR / JSON
    # -----------------------------------------

    try:

        text = raw.decode("utf-8").strip()

        if len(text) % 2 == 0:

            decoded_bytes = bytes.fromhex(text)

            # CBOR
            if cbor2 is not None:

                try:
                    return cbor2.loads(
                        decoded_bytes
                    )

                except Exception:
                    pass

            # JSON
            try:

                return json.loads(
                    decoded_bytes.decode("utf-8")
                )

            except Exception:
                pass

    except Exception:
        pass


    return None


# =========================================================
# FIND CONSOLE ROWS
# =========================================================

def find_rows(obj):

    found = []

    def walk(value):

        if isinstance(value, dict):

            for key, child in value.items():

                if key.lower() in (
                    "rows",
                    "data",
                    "results",
                    "messages",
                    "updates"
                ):

                    if isinstance(child, list):

                        found.extend(child)

                walk(child)

        elif isinstance(value, list):

            for item in value:
                walk(item)

    walk(obj)

    return found


# =========================================================
# EXTRACT SAFE CONSOLE RECORDS
# =========================================================

def extract_console_records(data):

    rows = find_rows(data)

    records = []

    for row in rows:

        if not isinstance(row, dict):
            continue

        record = {
            "idx": (
                row.get("idx")
                or row.get("id")
                or row.get("index")
            ),

            "at_ms": (
                row.get("at_ms")
                or row.get("timestamp")
                or row.get("created_at")
            ),

            "range": (
                row.get("range")
                or row.get("prefix")
                or ""
            ),

            "sender": (
                row.get("sender")
                or row.get("service")
                or row.get("brand")
                or ""
            ),

            "country": (
                row.get("country")
                or row.get("country_name")
                or ""
            ),

            "operator": (
                row.get("operator")
                or row.get("network")
                or ""
            )
        }

        if not any(record.values()):
            continue

        records.append(record)

    return records


# =========================================================
# RECORD UNIQUE KEY
# =========================================================

def record_key(record):

    if record.get("idx") is not None:

        return f"idx:{record['idx']}"

    safe_data = {
        "at_ms": record.get("at_ms"),
        "range": record.get("range"),
        "sender": record.get("sender"),
        "country": record.get("country"),
        "operator": record.get("operator")
    }

    raw = json.dumps(
        safe_data,
        sort_keys=True,
        default=str
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


# =========================================================
# FORMAT SMS ACTIVITY
# =========================================================

def format_console_message(record):

    rng = tg_escape(
        record.get("range") or "Unknown"
    )

    sender = tg_escape(
        record.get("sender") or "Unknown"
    )

    country = tg_escape(
        record.get("country") or "Unknown"
    )

    operator = tg_escape(
        record.get("operator") or "Unknown"
    )

    at_ms = tg_escape(
        str(record.get("at_ms") or "Unknown")
    )

    return (
        "📩 <b>NEW ACTIVITY</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"📋 <b>Range:</b>\n"
        f"<code>{rng}</code>\n\n"

        f"📨 <b>Service:</b> {sender}\n"
        f"🌍 <b>Country:</b> {country}\n"
        f"📡 <b>Operator:</b> {operator}\n"
        f"🕒 <b>Time:</b> <code>{at_ms}</code>"
    )

# =========================================================
# CONSOLE REQUEST
# =========================================================

def get_console():

    url = f"{BASE_URL}/console"

    try:

        response = console_session.get(
            url,
            timeout=20
        )

        if not response.ok:

            print(
                "console HTTP error:",
                response.status_code,
                response.text[:300]
            )

            return None

        return decode_console_response(
            response
        )

    except Exception as e:

        print(
            "console error:",
            e
        )

        return None


# =========================================================
# INITIAL STATE
# =========================================================

def load_initial_ranges():

    print(
        "Initial range state: EMPTY "
        "(all current ranges will be sent)"
    )

    return set()


def load_initial_console_records():

    print(
        "Initial console state: EMPTY "
        "(all currently available records will be sent)"
    )

    return set()


# =========================================================
# MAIN
# =========================================================

def main():

    print(
        "Live Range + SMS Update Monitor Started"
    )

    if not API_KEY:
        print(
            "WARNING: ZEBRASMS_API_KEY is missing"
        )

    if not CONSOLE_TOKEN:
        print(
            "WARNING: ZEBRASMS_CONSOLE_TOKEN is missing"
        )

    if not TELEGRAM_BOT_TOKEN:
        print(
            "WARNING: TELEGRAM_BOT_TOKEN is missing"
        )

    if not TELEGRAM_CHAT_ID:
        print(
            "WARNING: TELEGRAM_CHAT_ID is missing"
        )

    print("Configuration OK")

    known_ranges = (
        load_initial_ranges()
    )

    known_console_records = (
        load_initial_console_records()
    )

    print("Initial data loaded")
    print()


    while True:

        # =================================================
        # LIVE RANGES
        # =================================================

        live_data = get_liveaccess()

        if live_data is not None:

            live_entries = (
                extract_live_entries(
                    live_data
                )
            )

            current_ranges = {
                entry["range"]
                for entry in live_entries
                if entry.get("range")
            }

            new_entries = [
                entry
                for entry in live_entries
                if (
                    entry.get("range")
                    and entry["range"]
                    not in known_ranges
                )
            ]


            for entry in new_entries:

                send_telegram(
                    format_live_range_message(
                        entry
                    )
                )

                known_ranges.add(
                    entry["range"]
                )


            known_ranges = (
                known_ranges
                & current_ranges
            ) | {
                entry["range"]
                for entry in new_entries
                if entry.get("range")
            }


            print(
                "Checked liveaccess | "
                f"{len(current_ranges)} ranges | "
                f"{len(new_entries)} new"
            )

        else:

            print(
                "Checked liveaccess | unavailable"
            )


        # =================================================
        # CONSOLE / SMS ACTIVITY
        # =================================================

        console_data = get_console()

        if console_data is not None:

            records = (
                extract_console_records(
                    console_data
                )
            )

            new_records = []


            for record in records:

                key = record_key(
                    record
                )

                if key not in known_console_records:

                    new_records.append(
                        record
                    )

                    known_console_records.add(
                        key
                    )


            for record in new_records:

                send_telegram(
                    format_console_message(
                        record
                    )
                )


            print(
                "Checked console | "
                f"{len(records)} records | "
                f"{len(new_records)} new"
            )

        else:

            print(
                "Checked console | unavailable"
            )


        # =================================================
        # WAIT
        # =================================================

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
