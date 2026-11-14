import os
import re

CATEGORIES = ("MED", "TRAP", "WATER", "SAFE", "OTHER")
MAX_TEXT = 60
MAX_PAYLOAD = 100


def sanitize(text: str) -> str:
    """Clean a message field to the project contract."""
    if text is None:
        return ""
    value = str(text)
    value = value.replace("\\|", "")
    value = value.replace('"', "")
    value = value.replace(",", "")
    value = value.replace("\r", " ")
    value = value.replace("\n", " ")
    value = "".join(ch for ch in value if ord(ch) < 128)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:MAX_TEXT]


def make_zone(building: str, floor: str, room: str | None) -> str:
    building = (building or "DUB").upper().replace(" ", "")
    floor = str(floor or "0").upper().replace(" ", "")
    room_value = (room or "X").upper().replace(" ", "")
    zone = f"{building}-{floor}-{room_value}"
    if len(zone) > 14:
        raise ValueError(f"Zone too long: {zone}")
    return zone


def encode(msg_id: str, zone: str, category: str, text: str) -> str:
    msg_id = str(msg_id or "").strip()
    zone = str(zone or "").strip()
    category = str(category or "").upper()
    cleaned = sanitize(text)
    if not msg_id:
        raise ValueError("msg_id is required")
    if category not in CATEGORIES:
        raise ValueError(f"Unsupported category: {category}")
    if not re.fullmatch(r"[A-Z0-9-]+", zone):
        raise ValueError(f"Invalid zone: {zone}")
    payload = f"{msg_id}|{zone}|{category}|{cleaned}"
    if len(payload.encode("utf-8")) > MAX_PAYLOAD:
        raise ValueError(f"Payload too large: {len(payload.encode('utf-8'))} bytes")
    return payload


def decode(payload: str) -> dict:
    if not payload or not isinstance(payload, str):
        raise ValueError("Payload is required")
    parts = payload.split("|")
    if len(parts) != 4:
        raise ValueError("Payload must contain exactly 4 fields")
    msg_id, zone, category, text = parts
    category = str(category).upper()
    if category not in CATEGORIES:
        raise ValueError(f"Unsupported category: {category}")
    if not zone:
        raise ValueError("Zone is required")
    zone = zone.strip()
    if len(zone) > 14:
        raise ValueError("Zone exceeds 14 characters")
    match = re.fullmatch(r"([A-Z0-9]+)-(\d+)-([A-Z0-9]+)", zone)
    if not match:
        raise ValueError(f"Invalid zone format: {zone}")
    building, floor, room = match.groups()
    cleaned = sanitize(text)
    return {
        "id": msg_id.strip(),
        "zone": zone,
        "building": building,
        "floor": floor,
        "room": room,
        "category": category,
        "text": cleaned,
    }


def next_id(node_id: str) -> str:
    node = (node_id or "A").upper().strip() or "A"
    if len(node) < 1 or not re.fullmatch(r"[A-Z]+", node):
        raise ValueError("node_id must be a letter or letters")
    counter_path = os.path.join(os.getcwd(), "counter.txt")
    current = 0
    if os.path.exists(counter_path):
        try:
            with open(counter_path, "r", encoding="utf-8") as fh:
                content = fh.read().strip()
            current = int(content) if content else 0
        except ValueError:
            current = 0
    current += 1
    with open(counter_path, "w", encoding="utf-8") as fh:
        fh.write(str(current))
    return f"{node}{current}"
