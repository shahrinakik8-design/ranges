#!/usr/bin/env python3
"""
Zebra SMS Console Range Monitor — Console-only

What this does:
- Calls ONLY the Zebra SMS /api/v1/console endpoint.
- Reads the explicit `range` field from `data.rows`.
- Accepts only masked ranges such as 26134XXX / 25567XXX.
- Never derives a range from a full phone number.
- Never calls /liveaccess.
- Never reads or forwards OTP/SMS message contents.
- Sends the exact masked range plus non-sensitive Console metadata to Telegram.
- On startup it sends the current snapshot once.
- Afterwards it sends a message whenever the Console snapshot changes.

Railway Variables:
    BOT_TOKEN
    GROUP_ID
    ZEBRA_MAUTH_TOKEN

Requirements:
    httpx>=0.27,<1
"""

import asyncio
import base64
import json
import os
import re
import struct
import time
from datetime import datetime

import httpx


# =========================
# Railway environment
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROUP_ID_RAW = os.getenv("GROUP_ID", "-1004415108815").strip()
ZEBRA_MAUTH_TOKEN = os.getenv("ZEBRA_MAUTH_TOKEN", "").strip()

# Inline buttons shown under each Telegram notification.
# Change these two URLs if you use different destinations.
NUMBER_BOT_URL = "https://t.me/testjonson2_bot"
MAIN_CHANNEL_URL = "https://t.me/otpmastersgrp"

CONSOLE_URL = "https://zebrasms.com/api/v1/console"

POLL_SECONDS = 5
TIMEOUT = 20

# Explicitly require the masked form returned by Console.
RANGE_RE = re.compile(r"^\d+X+$")


def parse_chat_id(value: str):
    """Telegram chat IDs can be negative integers."""
    try:
        return int(value)
    except ValueError:
        return value


GROUP_ID = parse_chat_id(GROUP_ID_RAW)


# =========================
# Minimal CBOR decoder
# =========================
class CborDecodeError(Exception):
    pass


def cbor_uint(data: bytes, pos: int, additional: int):
    if additional < 24:
        return additional, pos

    nbytes = {24: 1, 25: 2, 26: 4, 27: 8}.get(additional)
    if nbytes is None:
        raise CborDecodeError("Unsupported CBOR integer width")

    end = pos + nbytes
    if end > len(data):
        raise CborDecodeError("Truncated CBOR integer")

    return int.from_bytes(data[pos:end], "big"), end


def decode_cbor(data: bytes, pos: int = 0):
    if pos >= len(data):
        raise CborDecodeError("Unexpected end of CBOR")

    initial = data[pos]
    pos += 1

    major = initial >> 5
    additional = initial & 31

    if major in (0, 1):
        value, pos = cbor_uint(data, pos, additional)
        return (value if major == 0 else -1 - value), pos

    if major in (2, 3):
        length, pos = cbor_uint(data, pos, additional)
        if pos + length > len(data):
            raise CborDecodeError("Truncated CBOR string")

        raw = data[pos:pos + length]
        pos += length

        if major == 2:
            return raw, pos

        return raw.decode("utf-8", errors="replace"), pos

    if major == 4:
        length, pos = cbor_uint(data, pos, additional)
        result = []

        for _ in range(length):
            value, pos = decode_cbor(data, pos)
            result.append(value)

        return result, pos

    if major == 5:
        length, pos = cbor_uint(data, pos, additional)
        result = {}

        for _ in range(length):
            key, pos = decode_cbor(data, pos)
            value, pos = decode_cbor(data, pos)

            if isinstance(key, bytes):
                key = key.decode("utf-8", errors="replace")

            result[key] = value

        return result, pos

    if major == 7:
        if additional == 20:
            return False, pos
        if additional == 21:
            return True, pos
        if additional in (22, 23):
            return None, pos

        if additional == 27:
            end = pos + 8
            if end > len(data):
                raise CborDecodeError("Truncated CBOR float")
            return struct.unpack(">d", data[pos:end])[0], end

    raise CborDecodeError(
        f"Unsupported CBOR type major={major} additional={additional}"
    )


def decode_response(response: httpx.Response):
    """Decode JSON or the CBOR representation used by the Console."""
    raw = response.content
    content_type = response.headers.get("content-type", "").lower()

    # DevTools/copy output can expose a CBOR body as a data URI.
    text = raw.decode("utf-8", errors="replace").strip()
    if text.startswith("data:application/cbor;base64,"):
        raw = base64.b64decode(text.split(",", 1)[1])
        content_type = "application/cbor"

    if "json" in content_type:
        return json.loads(raw.decode("utf-8"))

    if "cbor" in content_type:
        value, _ = decode_cbor(raw)
        return value

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Some proxies omit content-type.
        value, _ = decode_cbor(raw)
        return value


# =========================
# Console parsing
# =========================
def get_rows(payload):
    """
    The captured Console response uses:
        data.rows = [...]
    We deliberately use that collection first.
    """
    if isinstance(payload, dict):
        data = payload.get("data")

        if isinstance(data, dict) and isinstance(data.get("rows"), list):
            return data["rows"]

        if isinstance(data, list):
            return data

    if isinstance(payload, list):
        return payload

    return []


def explicit_range_from_row(row):
    """Return the explicit masked range only; never manufacture one."""
    if not isinstance(row, dict):
        return None

    value = row.get("range")

    if value in (None, ""):
        return None

    value = str(value).strip().upper()

    if not RANGE_RE.fullmatch(value):
        return None

    return value


def console_rows(payload):
    """Return Console rows with explicit masked ranges only."""
    rows = get_rows(payload)
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        range_value = explicit_range_from_row(row)
        if not range_value:
            continue
        result.append(row)
    return result

def row_signature(row):
    """Stable identifier for a Console event/row."""
    for key in ("idx", "id", "_id", "at_ms", "created_at", "timestamp"):
        value = row.get(key) if isinstance(row, dict) else None
        if value not in (None, ""):
            return f"{key}:{value}"
    # Fallback: use the whole row, but only for non-sensitive metadata/range.
    return json.dumps(
        {"range": explicit_range_from_row(row)},
        sort_keys=True,
    )


async def fetch_console(client: httpx.AsyncClient):
    response = await client.get(
        CONSOLE_URL,
        headers={
            "MAuth": ZEBRA_MAUTH_TOKEN,
            "Accept": "application/cbor, application/json, text/plain, */*",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": "https://zebrasms.com/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/152.0.0.0 Safari/537.36"
            ),
        },
    )

    response.raise_for_status()
    payload = decode_response(response)

    return console_rows(payload)


# =========================
# Telegram
# =========================
async def telegram_call(client, method, payload=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    if payload is None:
        response = await client.get(url)
    else:
        response = await client.post(url, json=payload)

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data}")

    return data.get("result")


def first_present(row, keys):
    """Return the first non-empty field from a Console row."""
    if not isinstance(row, dict):
        return None

    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            if isinstance(value, dict):
                nested = first_present(
                    value,
                    ("name", "label", "title", "value"),
                )
                if nested not in (None, ""):
                    return nested
            else:
                return value

    return None


def row_metadata(row):
    """
    Extract only non-sensitive display metadata:
    service, country, operator, and timestamp.

    SMS/OTP/message fields are intentionally ignored.
    """
    service = first_present(
        row,
        (
            "sender",
            "service",
            "service_name",
            "serviceName",
            "app",
            "application",
        ),
    )

    country = first_present(
        row,
        (
            "country",
            "country_name",
            "countryName",
            "nation",
        ),
    )

    operator = first_present(
        row,
        (
            "operator",
            "operator_name",
            "operatorName",
            "carrier",
            "network",
        ),
    )

    raw_timestamp = first_present(
        row,
        (
            "at_ms",
            "timestamp",
            "created_at",
            "createdAt",
            "time",
        ),
    )

    display_time = None
    if raw_timestamp not in (None, ""):
        try:
            numeric = float(raw_timestamp)
            if numeric > 10_000_000_000:
                numeric /= 1000
            display_time = datetime.fromtimestamp(numeric).strftime("%H:%M:%S")
        except (TypeError, ValueError, OSError, OverflowError):
            display_time = str(raw_timestamp)

    return {
        "service": str(service).strip() if service else None,
        "country": str(country).strip() if country else None,
        "operator": str(operator).strip() if operator else None,
        "time": display_time or datetime.now().strftime("%H:%M:%S"),
    }


async def send_range(client, row, reason):
    """
    Send the explicit masked range plus non-sensitive Console metadata.

    Never forwards message/OTP/code fields.
    """
    range_value = explicit_range_from_row(row)
    if not range_value:
        return

    meta = row_metadata(row)

    lines = [
        "🟢 NEW ACTIVE RANGE 🟢",
        "",
        f"⏰ Time: {meta['time']}",
        f"📌 Range: {range_value}",
    ]

    if meta["service"]:
        lines.append(f"⚙️ Service: {meta['service']}")

    if meta["country"]:
        lines.append(f"🌍 Country: {meta['country']}")

    if meta["operator"]:
        lines.append(f"📡 Operator: {meta['operator']}")

    text = "\n".join(lines)

    result = await telegram_call(
        client,
        "sendMessage",
        {
            "chat_id": GROUP_ID,
            "text": text,
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {
                            "text": "🤖 Number Bot",
                            "url": NUMBER_BOT_URL,
                        },
                        {
                            "text": "📢 Main Channel",
                            "url": MAIN_CHANNEL_URL,
                        },
                    ]
                ]
            },
        },
    )

    message_id = result.get("message_id") if isinstance(result, dict) else None
    print(
        f"[Telegram] sent reason={reason} "
        f"message_id={message_id} range={range_value} "
        f"service={meta['service']!r} country={meta['country']!r} "
        f"operator={meta['operator']!r}"
    )


# =========================
# Main
# =========================
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Missing Railway variable: BOT_TOKEN")

    if not ZEBRA_MAUTH_TOKEN:
        raise RuntimeError("Missing Railway variable: ZEBRA_MAUTH_TOKEN")

    print("Zebra Console Range Monitor starting...")
    print(f"Console URL: {CONSOLE_URL}")
    print(f"Group ID: {GROUP_ID}")
    print(f"Poll: {POLL_SECONDS}s")
    print("Source: ONLY /console")
    print("Range source: data.rows[*].range")
    print("Phone numbers/OTP messages: NOT READ")
    print(f"Number Bot button: {NUMBER_BOT_URL}")
    print(f"Main Channel button: {MAIN_CHANNEL_URL}")

    timeout = httpx.Timeout(
        connect=10,
        read=TIMEOUT,
        write=10,
        pool=10,
    )

    limits = httpx.Limits(
        max_connections=10,
        max_keepalive_connections=5,
    )

    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:

        # Telegram destination check.
        me = await telegram_call(client, "getMe")
        print(
            f"[Telegram] bot=@{me.get('username')} "
            f"id={me.get('id')}"
        )

        chat = await telegram_call(
            client,
            "getChat",
            {"chat_id": GROUP_ID},
        )
        print(
            f"[Telegram] target id={chat.get('id')} "
            f"type={chat.get('type')} "
            f"title={chat.get('title', '')!r}"
        )

        seen_events = set()

        while True:
            try:
                rows = await fetch_console(client)

                current_ranges = [explicit_range_from_row(r) for r in rows]
                print(
                    f"[{time.strftime('%H:%M:%S')}] "
                    f"Console rows={len(rows)} ranges={current_ranges}"
                )

                # Process rows in the same order as the Console response.
                # On the first poll, announce the currently visible rows once.
                # On later polls, announce only rows/events not seen before.
                new_rows = []
                for row in rows:
                    sig = row_signature(row)
                    if sig not in seen_events:
                        new_rows.append(row)
                        seen_events.add(sig)

                # Prevent unbounded memory growth while keeping recent events.
                if len(seen_events) > 5000:
                    seen_events = set(list(seen_events)[-2500:])

                for row in reversed(new_rows):
                    if explicit_range_from_row(row):
                        await send_range(
                            client,
                            row,
                            "new_console_row",
                        )

            except httpx.HTTPStatusError as exc:
                print(
                    f"[ERROR] HTTP {exc.response.status_code} "
                    f"from {exc.request.url}"
                )

            except Exception as exc:
                print(
                    f"[ERROR] {type(exc).__name__}: {exc}"
                )

            await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped.")
