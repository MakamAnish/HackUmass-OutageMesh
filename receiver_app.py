"""Laptop B (responder post): LoRa receive -> SQLite -> dashboard. Run: python receiver_app.py"""
import json
import logging
import os
import queue
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from db import DB_PATH, init as init_db, list_messages, set_status
from ingest import ingest
from protocol import encode_ack, sanitize
import radio_worker

_sim_n = 0
_ack_queue = queue.Queue()

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")

FLASK_PORT = int(os.getenv("FLASK_PORT", "5001"))
MAP_PATH = Path(__file__).resolve().parent / "static" / "floorplans" / "help_points.json"

app = Flask(__name__)
init_db()


def _send_ack_queue() -> None:
    while True:
        msg_id, ack_status, note = _ack_queue.get()
        try:
            payload = encode_ack(msg_id, ack_status, note)
            for attempt in range(1, 4):
                if radio_worker.send(payload):
                    logging.getLogger("receiver").info(
                        "%s ACK sent for %s", ack_status, msg_id
                    )
                    break
                if attempt < 3:
                    time.sleep(1)
            else:
                logging.getLogger("receiver").warning(
                    "%s ACK failed after 3 tries for %s", ack_status, msg_id
                )
        except Exception:
            logging.getLogger("receiver").exception(
                "Could not send %s ACK for %s", ack_status, msg_id
            )
        finally:
            _ack_queue.task_done()


threading.Thread(target=_send_ack_queue, name="ack_sender", daemon=True).start()
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
    ack_statuses = {"ack": "HEARD", "on_way": "ON_WAY", "resolved": "DONE"}
    if status not in ack_statuses:
        return jsonify({"ok": False, "error": "status must be ack, on_way, or resolved"}), 400
    status_value = "ack" if status == "ack" else "resolved" if status == "resolved" else "on_way"
    set_status(mid, status_value)
    _ack_queue.put((mid.upper(), ack_statuses[status], sanitize(reply_text)[:50]))
    return jsonify({"ok": True, "queued": True})


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
