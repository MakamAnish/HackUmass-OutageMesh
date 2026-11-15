"""Laptop B (responder post): LoRa receive -> SQLite -> dashboard. Run: python receiver_app.py"""
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from db import DB_PATH, init as init_db, list_messages, set_status
from ingest import ingest
from protocol import encode_ack, encode_done
import radio_worker

_sim_n = 0

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")

FLASK_PORT = int(os.getenv("FLASK_PORT", "5001"))
MAP_PATH = Path(__file__).resolve().parent / "static" / "floorplans" / "help_points.json"

app = Flask(__name__)
init_db()
radio_worker.start(ingest)
logging.getLogger("receiver").info("radio worker started")


@app.get("/")
def home():
    return render_template("dashboard.html")


@app.get("/api/messages")
def messages():
    return jsonify(list_messages(since=request.args.get("since")))


@app.get("/api/map")
def api_map():
    """Fixed Wio / help-point positions on floor maps (not phone GPS)."""
    with MAP_PATH.open(encoding="utf-8") as fh:
        return jsonify(json.load(fh))


@app.post("/api/messages/<mid>/status")
def api_set_status(mid):
    data = request.get_json(silent=True) or {}
    status = data.get("status") or request.form.get("status")
    reply_text = (data.get("reply_text") or request.form.get("reply_text") or "").strip()
    if status not in {"ack", "resolved"}:
        return jsonify({"ok": False, "error": "status must be 'ack' or 'resolved'"}), 400
    set_status(mid, status)
    radio_ok = None
    if status == "ack":
        try:
            radio_ok = radio_worker.send(encode_ack(mid, reply_text))
            logging.getLogger("receiver").info(
                "ACK sent for %s reply=%r ok=%s", mid, reply_text, radio_ok
            )
        except Exception as e:
            radio_ok = False
            logging.getLogger("receiver").warning("ACK radio send failed for %s: %s", mid, e)
    elif status == "resolved":
        try:
            radio_ok = radio_worker.send(encode_done(mid, reply_text))
            logging.getLogger("receiver").info(
                "DONE sent for %s note=%r ok=%s", mid, reply_text, radio_ok
            )
        except Exception as e:
            radio_ok = False
            logging.getLogger("receiver").warning("DONE radio send failed for %s: %s", mid, e)
    return jsonify({"ok": True, "radio_ack": radio_ok})


@app.get("/api/health")
def health():
    rows = list_messages()
    return jsonify(
        {
            "ok": True,
            "messages": len(rows),
            "radio": radio_worker.health(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.post("/api/simulate")
def simulate():
    global _sim_n
    _sim_n += 1
    mid = f"T{(_sim_n % 99999) or 1}"
    ranges = (9.0, 16.0, 24.0, 31.0)
    range_m = ranges[(_sim_n - 1) % len(ranges)]
    row = ingest(f"{mid}|CC-1-X|MED|needs fridge for insulin|{range_m:.1f}", rssi=-55, snr=12.0)
    return jsonify({"ok": True, "id": mid, "stored": row is not None})


@app.post("/api/clear")
def clear():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM messages")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False)
