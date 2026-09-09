
import os
import time
import json
import hashlib
import requests

try:
    import cbor2
except ImportError:
    cbor2 = None


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://api.zebrasms.com/api/v1"

API_KEY = os.getenv("ZEBRASMS_API_KEY")
CONSOLE_TOKEN = os.getenv("ZEBRASMS_CONSOLE_TOKEN")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CHECK_INTERVAL = int(
    os.getenv("CHECK_INTERVAL", "5")
)


# ============================================================
# HTTP SESSIONS
# ============================================================

# Developer API session
api_session = requests.Session()

api_session.headers.update({
    "MAuth": API_KEY or "",
    "Accept": "*/*",
    "User-Agent": "Mozilla/5.0"
})


# Web console session
console_session = requests.Session()

console_session.headers.update({
    "MAuth": CONSOLE_TOKEN or "",
    "Accept": "*/*",
    "Origin": "https://zebrasms.com",
    "Referer": "https://zebrasms.com/",
    "User-Agent": "Mozilla/5.0"
})


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):
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
                retry_after = (
                    response.json()
                    .get("parameters", {})
                    .get("retry_after", 5)
                )

                print(
                    f"Telegram rate limit. "
                    f"Waiting {retry_after}s..."
                )

                time.sleep(int(retry_after))

                return send_telegram(text)

            except Exception:
                return False

        if response.status_code != 200:
            print(
                "Telegram HTTP error:",
                response.status_code
            )
            print(
                response.text[:300]
            )
            return False

        return True

    except Exception as e:
        print(
            "Telegram error:",
            e
        )
        return False


# ============================================================
# LIVEACCESS API
# ============================================================

def get_liveaccess():

    try:
        response = api_session.get(
            f"{BASE_URL}/publicapi/liveaccess",
            timeout=20
        )

        if response.status_code != 200:
            print(
                "liveaccess HTTP error:",
                response.status_code
            )
            return None

        try:
            return response.json()

        except Exception:
            print(
                "liveaccess returned invalid JSON"
            )
            return None

    except Exception as e:
        print(
            "liveaccess error:",
            e
        )
        return None


# ============================================================
# GENERIC RANGE EXTRACTION
# ============================================================

def extract_live_ranges(data):

    found = []

    def walk(obj):

        if isinstance(obj, dict):

            # Direct range fields
            for key in (
                "range",
                "ranges",
                "prefix",
                "prefixes"
            ):

                value = obj.get(key)

                if isinstance(value, str):
                    found.append(value)

                elif isinstance(value, list):
                    for item in value:

                        if isinstance(
                            item,
                            str
                        ):
                            found.append(item)

                        elif isinstance(
                            item,
                            dict
                        ):
                            walk(item)

            # Continue recursively
            for key, value in obj.items():

                if key not in (
                    "range",
                    "ranges",
                    "prefix",
                    "prefixes"
                ):
                    walk(value)

        elif isinstance(obj, list):

            for item in obj:
                walk(item)

    walk(data)

    # Clean and deduplicate
    result = []

    seen = set()

    for value in found:

        value = str(value).strip()

        if not value:
            continue

        if value in seen:
            continue

        seen.add(value)
        result.append(value)

    return result


# ============================================================
# DYNAMIC LIVE RANGE MESSAGE
# ============================================================

def format_live_range_message(
    range_value,
    raw_item=None
):

    service = "Unknown"
    country = "Unknown"
    operator = "Unknown"

    if isinstance(raw_item, dict):

        service = (
            raw_item.get("service")
            or raw_item.get("sender")
            or raw_item.get("brand")
            or "Unknown"
        )

        country = (
            raw_item.get("country")
            or raw_item.get("country_name")
            or "Unknown"
        )

        operator = (
            raw_item.get("operator")
            or raw_item.get("network")
            or "Unknown"
        )

    return (
        "🟢 New Live Range\n\n"
        f"⚙️ Service: {service}\n"
        f"🌍 Country: {country}\n"
        f"📊 Range: {range_value}\n"
        f"📡 Operator: {operator}"
    )


# ============================================================
# CONSOLE RESPONSE DECODER
# ============================================================

def decode_console_response(response):

    raw = response.content

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    try:
        return response.json()

    except Exception:
        pass


    # --------------------------------------------------------
    # Raw CBOR
    # --------------------------------------------------------

    if cbor2:

        try:
            return cbor2.loads(raw)

        except Exception:
            pass


    # --------------------------------------------------------
    # Hex encoded CBOR / JSON
    # --------------------------------------------------------

    try:

        text = raw.decode(
            "utf-8",
            errors="ignore"
        ).strip()

        text = text.strip('"')

        if (
            len(text) > 10
            and len(text) % 2 == 0
            and all(
                char in
                "0123456789abcdefABCDEF"
                for char in text
            )
        ):

            decoded = bytes.fromhex(text)

            if cbor2:

                try:
                    return cbor2.loads(
                        decoded
                    )

                except Exception:
                    pass

            try:
                return json.loads(
                    decoded.decode(
                        "utf-8"
                    )
                )

            except Exception:
                pass

    except Exception:
        pass


    return None


# ============================================================
# FIND CONSOLE ROWS
# ============================================================

def find_rows(obj):

    if isinstance(obj, dict):

        rows = obj.get("rows")

        if isinstance(rows, list):
            return rows

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


# ============================================================
# CONSOLE RECORD EXTRACTION
# ============================================================

def extract_console_records(data):

    rows = find_rows(data)

    if not rows:
        return []

    records = []

    for row in rows:

        if not isinstance(row, dict):
            continue

        # ----------------------------------------------------
        # Metadata only.
        #
        # Intentionally NOT reading:
        # message
        # sms
        # otp
        # code
        # verification text
        # phone number
        # ----------------------------------------------------

        record = {
            "idx": row.get("idx"),
            "eat_ms": row.get("eat_ms"),
            "range": row.get("range"),
            "sender": row.get("sender"),
            "country": row.get("country"),
            "operator": row.get("operator")
        }

        if not any([
            record["idx"],
            record["range"],
            record["sender"]
        ]):
            continue

        records.append(record)

    return records


# ============================================================
# UNIQUE RECORD KEY
# ============================================================

def record_key(record):

    # Console index is preferred
    if record.get("idx") is not None:
        return (
            f"idx:{record['idx']}"
        )

    safe_data = "|".join([
        str(record.get("eat_ms") or ""),
        str(record.get("range") or ""),
        str(record.get("sender") or ""),
        str(record.get("country") or ""),
        str(record.get("operator") or "")
    ])

    return hashlib.sha256(
        safe_data.encode("utf-8")
    ).hexdigest()


# ============================================================
# CONSOLE TELEGRAM MESSAGE
# ============================================================

def format_console_message(record):

    sender = (
        record.get("sender")
        or "Unknown"
    )

    country = (
        record.get("country")
        or "Unknown"
    )

    range_value = (
        record.get("range")
        or "Unknown"
    )

    operator = (
        record.get("operator")
        or "Unknown"
    )

    return (
        "🔔 New SMS Update\n\n"
        f"⚙️ Service: {sender}\n"
        f"🌍 Country: {country}\n"
        f"📊 Range: {range_value}\n"
        f"📡 Operator: {operator}\n\n"
        "📩 SMS received\n"
        "🔒 Message content is not "
        "processed by this bot."
    )


# ============================================================
# CONSOLE API
# ============================================================

def get_console():

    if not CONSOLE_TOKEN:

        print(
            "ZEBRASMS_CONSOLE_TOKEN "
            "is not configured"
        )

        return None

    try:

        response = console_session.get(
            f"{BASE_URL}/console",
            timeout=20
        )

        if response.status_code != 200:

            print(
                "console HTTP error:",
                response.status_code
            )

            if response.status_code == 401:

                print(
                    "Console authentication "
                    "failed. The console token "
                    "may be expired."
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


# ============================================================
# INITIAL LIVE RANGE STATE
# ============================================================

def load_initial_ranges():

    data = get_liveaccess()

    if data is None:

        print(
            "Initial liveaccess data "
            "unavailable"
        )

        return set()

    ranges = extract_live_ranges(
        data
    )

    known = set(
        str(item).strip()
        for item in ranges
        if str(item).strip()
    )

    print(
        f"Loaded {len(known)} existing ranges"
    )

    return known


# ============================================================
# INITIAL CONSOLE STATE
# ============================================================

def load_initial_console_records():

    data = get_console()

    if data is None:

        print(
            "Initial console data unavailable"
        )

        return set()

    records = extract_console_records(
        data
    )

    known = set()

    for record in records:

        known.add(
            record_key(record)
        )

    print(
        f"Loaded {len(known)} existing "
        f"SMS updates"
    )

    return known


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    print(
        "Live Range + SMS Update "
        "Monitor Started"
    )


    # --------------------------------------------------------
    # Configuration check
    # --------------------------------------------------------

    missing = []

    if not API_KEY:
        missing.append(
            "ZEBRASMS_API_KEY"
        )

    if not CONSOLE_TOKEN:
        missing.append(
            "ZEBRASMS_CONSOLE_TOKEN"
        )

    if not TELEGRAM_BOT_TOKEN:
        missing.append(
            "TELEGRAM_BOT_TOKEN"
        )

    if not TELEGRAM_CHAT_ID:
        missing.append(
            "TELEGRAM_CHAT_ID"
        )


    if missing:

        print(
            "Missing environment variables:"
        )

        for name in missing:
            print(
                f" - {name}"
            )

        return


    print(
        "Configuration OK"
    )


    # --------------------------------------------------------
    # Load existing state
    # --------------------------------------------------------

    known_ranges = (
        load_initial_ranges()
    )

    known_console = (
        load_initial_console_records()
    )

    print(
        "Initial data loaded"
    )


    # --------------------------------------------------------
    # Monitoring
    # --------------------------------------------------------

    while True:

        try:

            # =================================================
            # LIVEACCESS
            # =================================================

            live_data = get_liveaccess()

            if live_data is not None:

                current_ranges = (
                    extract_live_ranges(
                        live_data
                    )
                )

                new_ranges = []

                for range_value in current_ranges:

                    range_value = (
                        str(range_value)
                        .strip()
                    )

                    if not range_value:
                        continue

                    # Dynamic:
                    # Any range is accepted.
                    if range_value not in known_ranges:

                        known_ranges.add(
                            range_value
                        )

                        new_ranges.append(
                            range_value
                        )


                for range_value in new_ranges:

                    message = (
                        format_live_range_message(
                            range_value
                        )
                    )

                    send_telegram(
                        message
                    )


                print(
                    f"Checked liveaccess | "
                    f"{len(current_ranges)} ranges | "
                    f"{len(new_ranges)} new"
                )

            else:

                print(
                    "Checked liveaccess | "
                    "unavailable"
                )


            # =================================================
            # CONSOLE
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

                    if key not in known_console:

                        known_console.add(
                            key
                        )

                        new_records.append(
                            record
                        )


                for record in new_records:

                    message = (
                        format_console_message(
                            record
                        )
                    )

                    send_telegram(
                        message
                    )


                print(
                    f"Checked console | "
                    f"{len(records)} updates | "
                    f"{len(new_records)} new"
                )

            else:

                print(
                    "Checked console | "
                    "unavailable"
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


        except KeyboardInterrupt:

            print(
                "Bot stopped."
            )

            break


        except Exception as e:

            print(
                "Main loop error:",
                e
            )

            time.sleep(
                CHECK_INTERVAL
            )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
