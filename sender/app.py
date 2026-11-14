"""Laptop A: form -> radio -> confirmation page. Run: python sender/app.py"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv

from protocol import CATEGORIES, encode, make_zone, next_id, sanitize
from radio import Radio

load_dotenv()

PORT = os.getenv("RADIO_PORT", "")
BUILDING = os.getenv("BUILDING", "DUB")
app = Flask(__name__, static_folder="static")

radio = None
if PORT:
    try:
        radio = Radio(PORT)
        radio.setup(receiver=False)
        print("Radio ready on", PORT)
    except Exception as exc:  # pragma: no cover - hardware may be absent in tests
        print(f"Radio setup failed: {exc}")
        radio = None
else:
    print("DRY RUN: no RADIO_PORT set; messages are printed, not sent")

ZONES = ["F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12", "F13", "F14", "X"]
CATS = {"MED": "Medical", "TRAP": "Trapped", "WATER": "Water", "SAFE": "Safe", "OTHER": "Other"}


@app.get("/")
def index():
    return render_template("form.html", zones=ZONES, cats=CATS)


@app.post("/send")
def send():
    payload = request.get_json(force=True, silent=True) or {}
    if not payload and request.form:
        payload = request.form.to_dict()

    floor = str(payload.get("floor") or "3")
    room = payload.get("room")
    category = str((payload.get("category") or "MED")).upper()
    text = sanitize(payload.get("text") or "")

    if not text:
        return jsonify({"ok": False, "error": "Type a short message first."}), 400
    if category not in CATEGORIES:
        category = "OTHER"

    zone = make_zone(BUILDING, floor, room)
    msg_id = next_id(os.getenv("NODE_ID", "A"))
    packet = encode(msg_id, zone, category, text)
    ok = True
    if radio is not None:
        ok = bool(radio.send(packet))
    else:
        print("SEND:", packet)

    if not ok:
        return jsonify({"ok": False, "error": "Radio did not confirm. Try again."}), 500
    return jsonify({"ok": True, "id": msg_id, "zone": zone, "payload": packet})


@app.get("/sent/<msg_id>")
def sent(msg_id):
    return f"<html><body><h1>Message {msg_id} sent</h1></body></html>"


@app.get("/api/building")
def building():
    with open(os.path.join(os.path.dirname(__file__), "static", "floorplans", f"{BUILDING}.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


@app.get("/health")
def health():
    if radio is not None and hasattr(radio, "health"):
        return jsonify(radio.health())
    return jsonify({"ok": True, "mode": "dry", "last_rx": None, "error": None})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
