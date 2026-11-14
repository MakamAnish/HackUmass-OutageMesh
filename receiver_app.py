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
import radio_worker

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
    if status not in {"ack", "resolved"}:
        return jsonify({"ok": False, "error": "status must be 'ack' or 'resolved'"}), 400
    set_status(mid, status)
    return jsonify({"ok": True})


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
    # No room from sender — location is help point A on the floor map.
    ingest("A99|DUB-3-X|MED|needs fridge for insulin", rssi=-55, snr=12.0)
    return jsonify({"ok": True})


@app.post("/api/clear")
def clear():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM messages")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False)
