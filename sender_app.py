"""Laptop A (help point): web form -> LoRa send. Run: python sender_app.py"""
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from protocol import CATEGORIES, encode, make_zone, next_id, sanitize
from radio import get_radio

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")

BUILDING = os.getenv("BUILDING", "DUB")
FLOOR = os.getenv("FLOOR", "3")
NODE_ID = os.getenv("NODE_ID", "A")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))

app = Flask(__name__)
_radio = None
log = logging.getLogger("sender")

CATS = {"MED": "Medical", "TRAP": "Trapped", "WATER": "Water", "SAFE": "Safe", "OTHER": "Other"}


def radio():
    global _radio
    if _radio is None:
        _radio = get_radio()
        log.info("radio ready mode=%s", _radio.health().get("mode"))
    return _radio


@app.get("/")
def index():
    return render_template("form.html", cats=CATS)


@app.post("/send")
def send():
    payload = request.get_json(silent=True) or {}
    if not payload and request.form:
        payload = request.form.to_dict()

    # Location defaults to this help point (BUILDING/FLOOR from .env).
    # Room is optional — blank means responders only know the Wio location.
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
        ok = radio().send(packet)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        log.exception("send failed")
        return jsonify({"ok": False, "error": f"Radio error: {e}"}), 500

    if not ok:
        return jsonify({"ok": False, "error": "Radio did not confirm. Try again."}), 500
    return jsonify({"ok": True, "id": msg_id, "zone": zone, "payload": packet})


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
    try:
        return jsonify(radio().health())
    except Exception as e:
        return jsonify({"ok": False, "mode": None, "last_rx": None, "error": str(e)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False)
