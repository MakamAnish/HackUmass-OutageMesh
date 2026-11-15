import threading

import db
import outgoing
import radio_worker


def test_ack_status_progress_and_duplicate_deduplication(tmp_path):
    path = str(tmp_path / "outgoing.db")
    outgoing.record(
        "A47",
        "CC-1-X",
        "MED",
        "need help",
        packet="A47|CC-1-X|MED|need help",
        db_path=path,
    )
    outgoing.set_send_state("A47", "sent", 1, db_path=path)

    assert outgoing.record_ack("A47", "HEARD", "We heard you", db_path=path)
    assert not outgoing.record_ack("A47", "HEARD", "duplicate", db_path=path)
    assert outgoing.record_ack("A47", "ON_WAY", "Help is coming", db_path=path)
    status = outgoing.get_status("A47", db_path=path)
    assert status["state"] == "ON_WAY"
    assert status["note"] == "Help is coming"

    assert outgoing.record_ack("A47", "DONE", "All clear", db_path=path)
    status = outgoing.get_status("A47", db_path=path)
    assert status["state"] == "DONE"
    assert status["note"] == "All clear"
    assert outgoing.get_status("missing", db_path=path) is None


def test_status_is_sending_until_transmit_succeeds(tmp_path):
    path = str(tmp_path / "outgoing.db")
    outgoing.record(
        "A48",
        "CC-1-X",
        "OTHER",
        "request",
        packet="A48|CC-1-X|OTHER|request",
        db_path=path,
    )
    status = outgoing.get_status("A48", db_path=path)
    assert status["state"] == "SENDING"

    outgoing.set_send_state("A48", "retrying", 1, db_path=path)
    status = outgoing.get_status("A48", db_path=path)
    assert status["state"] == "SENDING"
    assert status["send_state"] == "retrying"

    outgoing.set_send_state("A48", "sent", 2, db_path=path)
    assert outgoing.get_status("A48", db_path=path)["state"] == "SENT"


def test_receiver_status_route_enqueues_ack_without_sending_in_request(monkeypatch):
    monkeypatch.setattr(radio_worker, "start", lambda callback: None)
    monkeypatch.setattr(db, "init", lambda db_path=None: db_path)
    sent = []
    sent_event = threading.Event()

    def fake_send(payload):
        sent.append(payload)
        sent_event.set()
        return True

    monkeypatch.setattr(radio_worker, "send", fake_send)
    import receiver_app

    queued = []
    original_put = receiver_app._ack_queue.put

    def capture_put(item):
        queued.append(item)
        original_put(item)

    monkeypatch.setattr(receiver_app._ack_queue, "put", capture_put)
    monkeypatch.setattr(receiver_app, "set_status", lambda msg_id, status: True)

    response = receiver_app.app.test_client().post(
        "/api/messages/A47/status",
        json={"status": "on_way", "reply_text": 'Help, "soon"\n'},
    )

    assert response.status_code == 200
    assert response.json == {"ok": True, "queued": True}
    assert queued == [("A47", "ON_WAY", "Help soon")]
    assert sent_event.wait(1)
    assert sent == ["ACK|A47|ON_WAY|Help soon"]
    dashboard = receiver_app.app.test_client().get("/")
    assert b"Blackout" not in dashboard.data
    assert b"On my way" in dashboard.data
    assert b"TETHER" in dashboard.data
    assert b"alarmBtn" not in dashboard.data
    assert b"Alarm on" not in dashboard.data
    assert b"Alarm off" not in dashboard.data
    assert b"speechSynthesis" not in dashboard.data
    assert b"AudioContext" not in dashboard.data
    assert b"https://" not in dashboard.data


def test_sender_redirects_to_status_and_accepts_status_ack(monkeypatch):
    monkeypatch.setattr(radio_worker, "start", lambda callback: None)
    monkeypatch.setattr(outgoing, "init", lambda db_path=None: db_path)
    import sender_app

    sent_record = {}
    send_started = threading.Event()
    acks = []
    monkeypatch.setattr(sender_app, "next_id", lambda node_id: "A47")
    monkeypatch.setattr(
        outgoing,
        "record",
        lambda *args, **kwargs: sent_record.update(args=args, kwargs=kwargs),
    )
    monkeypatch.setattr(
        sender_app,
        "_send_request",
        lambda msg_id, packet: send_started.set(),
    )
    monkeypatch.setattr(
        outgoing,
        "record_ack",
        lambda msg_id, status, note: acks.append((msg_id, status, note)) or True,
    )
    client = sender_app.app.test_client()

    response = client.post(
        "/send",
        json={"room": "N151", "category": "MED", "text": "need help"},
    )
    assert response.status_code == 200
    assert response.json["redirect"] == "/status/A47"
    assert send_started.wait(1)

    sender_app._on_packet("ACK|A47|HEARD|We heard you", None, None)
    assert acks == [("A47", "HEARD", "We heard you")]

    monkeypatch.setattr(
        outgoing,
        "get_status",
        lambda msg_id: {
            "id": msg_id,
            "state": "HEARD",
            "note": "We heard you",
            "updated": "2026-11-14T20:00:00+00:00",
            "send_state": "sent",
            "send_attempts": 1,
        },
    )
    status = client.get("/api/status/A47")
    page = client.get("/status/A47")
    assert status.json["state"] == "HEARD"
    assert status.json["note"] == "We heard you"
    assert page.status_code == 200
    assert b"Send another request" in page.data
    assert b"My requests" in page.data
    assert b"nav-button" in page.data
    assert b"blackout" not in page.data.lower()
    assert b"Help is on the way" in page.data


def test_sender_retries_radio_send_three_times(monkeypatch):
    monkeypatch.setattr(radio_worker, "start", lambda callback: None)
    monkeypatch.setattr(outgoing, "init", lambda db_path=None: db_path)
    import sender_app

    calls = []
    states = []
    monkeypatch.setattr(
        sender_app.radio_worker,
        "send",
        lambda packet: calls.append(packet) or False,
    )
    monkeypatch.setattr(sender_app.time, "sleep", lambda delay: None)
    monkeypatch.setattr(
        outgoing,
        "set_send_state",
        lambda msg_id, state, attempts: states.append((state, attempts)),
    )

    sender_app._send_request("A49", "A49|CC-1-X|OTHER|help")

    assert calls == ["A49|CC-1-X|OTHER|help"] * 3
    assert states == [
        ("sending", 1),
        ("retrying", 2),
        ("retrying", 3),
        ("failed", 3),
    ]
