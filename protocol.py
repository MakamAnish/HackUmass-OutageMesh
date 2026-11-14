"""Message contract shared by sender, radio and receiver.

Payload: id|zone|category|text   e.g.  A47|DUB-3-F14|MED|needs fridge for insulin
"""
import re
import threading
from pathlib import Path

CATEGORIES = ("MED", "TRAP", "WATER", "SAFE", "OTHER")
MAX_TEXT = 60
MAX_ACK_TEXT = 40
MAX_PAYLOAD = 100
NO_ROOM = "X"

_ID_RE = re.compile(r"^[A-Z]\d{1,5}$")
_ZONE_RE = re.compile(r"^[A-Z0-9]{1,4}-[A-Z0-9]{1,3}-[A-Z0-9]{1,5}$")
_counter_lock = threading.Lock()


def sanitize(text: str) -> str:
    text = text.encode("ascii", "ignore").decode()
    text = re.sub(r'[|",\r\n\t]', " ", text)
    return re.sub(r" +", " ", text).strip()[:MAX_TEXT].strip()


def _part(value: str | None, max_len: int) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())[:max_len]


def make_zone(building: str, floor: str, room: str | None) -> str:
    zone = f"{_part(building, 4)}-{_part(floor, 3)}-{_part(room, 5) or NO_ROOM}"
    if not _ZONE_RE.match(zone):
        raise ValueError(f"bad zone: {zone!r}")
    return zone


def encode(msg_id: str, zone: str, category: str, text: str) -> str:
    if not _ID_RE.match(msg_id):
        raise ValueError(f"bad id: {msg_id!r}")
    if not _ZONE_RE.match(zone):
        raise ValueError(f"bad zone: {zone!r}")
    if category not in CATEGORIES:
        raise ValueError(f"bad category: {category!r}")
    payload = f"{msg_id}|{zone}|{category}|{sanitize(text)}"
    if len(payload.encode()) > MAX_PAYLOAD:
        raise ValueError(f"payload too long: {len(payload)} bytes")
    return payload


def decode(payload: str) -> dict:
    parts = payload.strip().split("|")
    if len(parts) != 4:
        raise ValueError(f"expected 4 fields, got {len(parts)}: {payload!r}")
    msg_id, zone, category, text = parts
    if not _ID_RE.match(msg_id):
        raise ValueError(f"bad id: {msg_id!r}")
    if not _ZONE_RE.match(zone):
        raise ValueError(f"bad zone: {zone!r}")
    if category not in CATEGORIES:
        raise ValueError(f"bad category: {category!r}")
    building, floor, room = zone.split("-")
    return {
        "id": msg_id,
        "zone": zone,
        "building": building,
        "floor": floor,
        "room": None if room == NO_ROOM else room,
        "category": category,
        "text": sanitize(text),
    }


def next_id(node_id: str, path: str | Path = "counter.txt") -> str:
    path = Path(path)
    with _counter_lock:
        n = int(path.read_text().strip() or 0) + 1 if path.exists() else 1
        path.write_text(str(n))
    return f"{node_id.upper()}{n}"


def _encode_status(kind: str, msg_id: str, reply_text: str = "") -> str:
    msg_id = str(msg_id or "").strip().upper()
    if not _ID_RE.match(msg_id):
        raise ValueError(f"bad id: {msg_id!r}")
    note = sanitize(reply_text)[:MAX_ACK_TEXT]
    payload = f"{kind}|{msg_id}|{note}" if note else f"{kind}|{msg_id}"
    if len(payload.encode()) > MAX_PAYLOAD:
        raise ValueError(f"{kind.lower()} too long: {len(payload)} bytes")
    return payload


def _decode_status(payload: str, kind: str) -> dict | None:
    parts = (payload or "").strip().split("|")
    if len(parts) < 2 or parts[0].upper() != kind:
        return None
    msg_id = parts[1].strip().upper()
    if not _ID_RE.match(msg_id):
        return None
    text = sanitize("|".join(parts[2:])) if len(parts) > 2 else ""
    return {"id": msg_id, "text": text[:MAX_ACK_TEXT]}


def encode_ack(msg_id: str, reply_text: str = "") -> str:
    """Responder -> help point: ACK|A47 or ACK|A47|optional reply."""
    return _encode_status("ACK", msg_id, reply_text)


def decode_ack(payload: str) -> dict | None:
    """Return {id, text} if payload is an ACK, else None."""
    return _decode_status(payload, "ACK")


def encode_done(msg_id: str, reply_text: str = "") -> str:
    """Responder -> help point: DONE|A47 or DONE|A47|optional note."""
    return _encode_status("DONE", msg_id, reply_text)


def decode_done(payload: str) -> dict | None:
    """Return {id, text} if payload is a DONE (resolved), else None."""
    return _decode_status(payload, "DONE")
