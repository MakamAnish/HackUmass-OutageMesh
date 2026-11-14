"""Laptop A (help point): web form -> LoRa send; listen for responder ACKs."""
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from protocol import CATEGORIES, decode_ack, encode, make_zone, next_id, sanitize
import radio_worker

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")

BUILDING = os.getenv("BUILDING", "DUB")
FLOOR = os.getenv("FLOOR", "3")
NODE_ID = os.getenv("NODE_ID", "A")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))

app = Flask(__name__)
log = logging.getLogger("sender")

CATS = {"MED": "Medical", "TRAP": "Trapped", "WATER": "Water", "SAFE": "Safe", "OTHER": "Other"}

# msg_id -> {"sent_at": iso, "acked_at": iso|None}
_status: dict[str, dict] = {}
_status_lock = threading.Lock()


def _on_packet(payload: str, rssi: int | None, snr: float | None) -> None:
    msg_id = decode_ack(payload)
    if not msg_id:
        return
    with _status_lock:
        row = _status.get(msg_id) or {"sent_at": None, "acked_at": None}
        row["acked_at"] = datetime.now(timezone.utc).isoformat()
        _status[msg_id] = row
    log.info("ACK received for %s (rssi=%s)", msg_id, rssi)


radio_worker.start(_on_packet)
log.info("listening for responder ACKs")


@app.get("/")
def index():
    return render_template("form.html", cats=CATS)


@app.post("/send")
def send():
    payload = request.get_json(silent=True) or {}
    if not payload and request.form:
        payload = request.form.to_dict()

    floor = str(payload.get("floor") or FLOOR)
    room = (payload.get("room") or payload.get("zone") or "").strip() or None
    category = str(payload.get("category") or "OTHER").upper()
    text = sanitize(payload.get("text") or "")

    if not text:
        return jsonify({"ok": False, "error": "Type a short message first."}), 400
    if category not in CATEGORIES:
        category = "OTHER"

    try:
        zone = make_zone(BUILDING, floor, room)
        msg_id = next_id(NODE_ID)
        packet = encode(msg_id, zone, category, text)
        ok = radio_worker.send(packet)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        log.exception("send failed")
        return jsonify({"ok": False, "error": f"Radio error: {e}"}), 500

    if not ok:
        return jsonify({"ok": False, "error": "Radio did not confirm. Try again."}), 500

    with _status_lock:
        _status[msg_id] = {
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "acked_at": None,
        }
    return jsonify({"ok": True, "id": msg_id, "zone": zone, "payload": packet})


@app.get("/api/ack/<msg_id>")
def api_ack(msg_id):
    with _status_lock:
        row = _status.get(msg_id.upper()) or _status.get(msg_id)
    if not row:
        return jsonify({"ok": True, "id": msg_id, "acked": False, "known": False})
    return jsonify(
        {
            "ok": True,
            "id": msg_id,
            "known": True,
            "acked": row.get("acked_at") is not None,
            "acked_at": row.get("acked_at"),
            "sent_at": row.get("sent_at"),
        }
    )


@app.get("/sent/<msg_id>")
def sent(msg_id):
    return render_template("sent.html", msg_id=msg_id)


@app.get("/api/building")
def building():
    path = Path(app.root_path) / "static" / "floorplans" / f"{BUILDING}.json"
    with path.open(encoding="utf-8") as fh:
        return jsonify(json.load(fh))


@app.get("/health")
def health():
    return jsonify(radio_worker.health())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False)
