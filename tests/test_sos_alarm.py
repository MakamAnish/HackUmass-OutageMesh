import logging
import threading
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

import db
import ingest
import radio_worker


class FakePlayer:
    def __init__(self):
        self.play_calls = 0
        self.stop_calls = 0
        self.fail = False
        self.playing = False
        self.last_path = None

    def play(self, path):
        if self.fail:
            raise RuntimeError("audio device unavailable")
        self.play_calls += 1
        self.playing = True
        self.last_path = path

    def stop(self):
        self.stop_calls += 1
        self.playing = False


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(radio_worker, "start", lambda callback: None)
    import receiver_app

    path = str(tmp_path / "messages.db")
    db.init(path)

    def outcome(row, db_path=path):
        return db.insert_outcome(row, db_path)

    monkeypatch.setattr(ingest, "insert_outcome", outcome)
    monkeypatch.setattr(
        receiver_app,
        "list_messages",
        lambda since=None, db_path=path: db.list_messages(since, db_path),
    )
    player = FakePlayer()
    receiver_app.alarm.stop()
    receiver_app.alarm._player = player
    receiver_app.alarm._active = False
    receiver_app.alarm._error = None
    box = SimpleNamespace(
        path=path,
        player=player,
        client=receiver_app.app.test_client(),
        app=receiver_app,
    )
    yield box
    receiver_app.alarm._player = player
    receiver_app.alarm.stop()


def test_insert_outcome_reports_a_new_row_and_insert_stays_compatible(tmp_path):
    path = str(tmp_path / "rows.db")
    stored, created = db.insert_outcome(
        {"id": "A101", "zone": "CC-1-X", "category": "SOS", "text": "Need immediate help"},
        path,
    )
    assert created is True
    assert stored["sos"] == 1
    assert stored["status"] == "new"

    again, created_again = db.insert_outcome(
        {"id": "A101", "zone": "CC-1-X", "category": "MED", "text": "replaced", "status": "ack"},
        path,
    )
    assert created_again is False
    assert again["category"] == "SOS"
    assert again["text"] == "Need immediate help"
    assert again["status"] == "new"
    assert again["received_at"] == stored["received_at"]

    via_insert = db.insert(
        {"id": "A101", "zone": "CC-1-X", "category": "SOS", "text": "Need immediate help"},
        path,
    )
    assert via_insert["id"] == "A101"
    assert not isinstance(via_insert, tuple)
    assert len(db.list_messages(db_path=path)) == 1


def test_new_sos_triggers_alarm(env):
    row = env.app.on_packet("A101|CC-1-X|SOS|Need immediate help", rssi=-70, snr=9.0)

    assert row["id"] == "A101"
    assert row["sos"] == 1
    assert row["category"] == "SOS"
    assert row["status"] == "new"
    assert row["level"] == "HIGH"
    assert env.app.alarm.is_active() is True
    assert env.player.play_calls == 1
    assert env.player.playing is True
    assert str(env.player.last_path).endswith("sos_alarm.wav")
    assert env.client.get("/api/alarm/status").json == {"active": True}
    stored = db.list_messages(db_path=env.path)
    assert stored[0]["id"] == "A101"
    assert stored[0]["sos"] == 1


@pytest.mark.parametrize(
    ("payload", "level"),
    [
        ("A201|CC-1-X|MED|Need medical assistance", "HIGH"),
        ("A202|CC-1-X|TRAP|door is stuck", "HIGH"),
        ("A203|CC-1-X|WATER|water in the hall", "MEDIUM"),
        ("A204|CC-1-X|SAFE|we are safe", "LOW"),
        ("A205|CC-1-X|OTHER|need a blanket", "LOW"),
        ("A206|CC-1-X|MED|patient is unconscious", "HIGH"),
    ],
)
def test_ordinary_messages_do_not_trigger_alarm(env, payload, level):
    row = env.app.on_packet(payload, rssi=-60, snr=8.0)

    assert row is not None
    assert row["category"] != "SOS"
    assert row["sos"] == 0
    assert row["level"] == level
    assert env.player.play_calls == 0
    assert env.player.playing is False
    assert env.app.alarm.is_active() is False
    assert env.client.get("/api/alarm/status").json == {"active": False}


def test_silence_stops_audio_without_changing_the_request_or_radio(env, monkeypatch):
    env.app.on_packet("A101|CC-1-X|SOS|Need immediate help")
    sent = []
    queued = []
    original_put = env.app._ack_queue.put

    def capture_put(item, *args, **kwargs):
        queued.append(item)
        return original_put(item, *args, **kwargs)

    monkeypatch.setattr(radio_worker, "send", lambda payload: sent.append(payload) or True)
    monkeypatch.setattr(env.app._ack_queue, "put", capture_put)

    response = env.client.post("/api/alarm/silence")
    again = env.client.post("/api/alarm/silence")

    assert response.status_code == 200
    assert response.json == {"ok": True, "active": False}
    assert again.json["ok"] is True
    assert again.json["active"] is False
    assert env.app.alarm.is_active() is False
    assert env.player.playing is False
    assert env.player.stop_calls >= 1
    stored = db.list_messages(db_path=env.path)
    assert len(stored) == 1
    assert stored[0]["id"] == "A101"
    assert stored[0]["sos"] == 1
    assert stored[0]["status"] == "new"
    assert stored[0]["level"] == "HIGH"
    assert sent == []
    assert queued == []


def test_new_sos_after_silence_starts_the_alarm_again(env):
    env.app.on_packet("A101|CC-1-X|SOS|Need immediate help")
    silenced = env.client.post("/api/alarm/silence")
    assert silenced.json["active"] is False
    assert env.player.playing is False

    row = env.app.on_packet("A103|CC-1-X|SOS|Second emergency")

    assert row["id"] == "A103"
    assert row["sos"] == 1
    assert env.player.play_calls == 2
    assert env.player.playing is True
    assert env.app.alarm.is_active() is True
    ids = [item["id"] for item in db.list_messages(db_path=env.path)]
    assert ids.count("A101") == 1
    assert ids.count("A103") == 1


def test_duplicate_sos_does_not_restart_a_silenced_alarm(env):
    payload = "A101|CC-1-X|SOS|Need immediate help"
    env.app.on_packet(payload)
    env.client.post("/api/alarm/silence")
    original = db.list_messages(db_path=env.path)[0]
    assert db.set_status("A101", "ack", env.path) is True

    again = env.app.on_packet(payload, rssi=-80, snr=4.0)

    assert again["id"] == "A101"
    assert env.player.play_calls == 1
    assert env.player.playing is False
    assert env.app.alarm.is_active() is False
    assert env.client.get("/api/alarm/status").json == {"active": False}
    stored = db.list_messages(db_path=env.path)
    assert len(stored) == 1
    assert stored[0]["status"] == "ack"
    assert stored[0]["sos"] == 1
    assert stored[0]["text"] == original["text"]
    assert stored[0]["received_at"] == original["received_at"]


def test_multiple_sos_messages_share_one_alarm(env):
    before = threading.active_count()

    first = env.app.on_packet("A101|CC-1-X|SOS|Need immediate help")
    second = env.app.on_packet("A102|CC-1-X|SOS|Another emergency")

    assert first["sos"] == 1
    assert second["sos"] == 1
    assert env.player.play_calls == 1
    assert env.player.playing is True
    assert env.app.alarm.is_active() is True
    assert threading.active_count() == before
    assert [thread.name for thread in threading.enumerate() if thread.name == "sos_alarm"] == []
    ids = {row["id"] for row in db.list_messages(db_path=env.path)}
    assert ids == {"A101", "A102"}


def test_dashboard_polling_does_not_retrigger_alarm(env):
    env.app.on_packet("A101|CC-1-X|SOS|Need immediate help")
    assert env.client.post("/api/alarm/silence").json["active"] is False

    for _ in range(5):
        messages = env.client.get("/api/messages")
        status = env.client.get("/api/alarm/status")
        page = env.client.get("/")
        assert messages.status_code == 200
        assert status.json == {"active": False}
        assert page.status_code == 200
        stored = messages.json
        assert len(stored) == 1
        assert stored[0]["id"] == "A101"
        assert stored[0]["sos"] == 1
        assert stored[0]["status"] == "new"

    assert env.player.play_calls == 1
    assert env.player.playing is False
    assert env.app.alarm.is_active() is False
    assert b"Silence Alarm" in page.data
    assert b"sosBanner" in page.data
    assert b"speechSynthesis" not in page.data
    assert b"AudioContext" not in page.data


def test_invalid_and_status_packets_do_not_trigger_alarm(env):
    for payload in (
        "",
        "not-a-packet",
        "A101|BAD|SOS|help",
        "ACK|A101|HEARD|ok",
        "DONE|A101|cleared",
        "ACK|A101|ON_WAY|coming",
    ):
        assert env.app.on_packet(payload, rssi=-40, snr=1.0) is None

    assert db.list_messages(db_path=env.path) == []
    assert env.player.play_calls == 0
    assert env.app.alarm.is_active() is False
    assert env.client.get("/api/alarm/status").json == {"active": False}


def test_audio_backend_failure_keeps_the_receiver_running(env, caplog):
    env.player.fail = True
    with caplog.at_level(logging.ERROR, logger="sos_alarm"):
        row = env.app.on_packet("A109|CC-1-X|SOS|help")
        follow = env.app.on_packet("A110|CC-1-X|SAFE|still here")

    assert row["id"] == "A109"
    assert row["sos"] == 1
    assert row["status"] == "new"
    assert follow["id"] == "A110"
    assert follow["category"] == "SAFE"
    assert env.app.alarm.is_active() is False
    assert env.player.playing is False
    status = env.client.get("/api/alarm/status").json
    assert status["active"] is False
    assert "audio device unavailable" in status["error"]
    assert "SOS alarm playback failed" in caplog.text
    stored = {item["id"]: item for item in db.list_messages(db_path=env.path)}
    assert stored["A109"]["sos"] == 1
    assert stored["A110"]["category"] == "SAFE"

    env.player.fail = False
    retried = env.app.on_packet("A111|CC-1-X|SOS|try again")
    assert retried["sos"] == 1
    assert env.app.alarm.is_active() is True
    assert env.player.play_calls == 1


def test_acknowledge_does_not_silence_or_clear_the_sos(env, monkeypatch):
    sent = []
    monkeypatch.setattr(radio_worker, "send", lambda payload: sent.append(payload) or True)
    monkeypatch.setattr(
        env.app,
        "set_status",
        lambda mid, status, db_path=env.path: db.set_status(mid, status, db_path),
    )
    env.app.on_packet("A101|CC-1-X|SOS|Need immediate help")

    response = env.client.post(
        "/api/messages/A101/status",
        json={"status": "ack", "reply_text": "We heard you"},
    )
    env.app._ack_queue.join()

    assert response.status_code == 200
    assert response.json == {"ok": True, "queued": True}
    assert env.app.alarm.is_active() is True
    assert env.player.playing is True
    assert env.player.play_calls == 1
    stored = db.list_messages(db_path=env.path)[0]
    assert stored["status"] == "ack"
    assert stored["sos"] == 1
    assert sent == ["ACK|A101|HEARD|We heard you"]


def test_simulate_does_not_trigger_alarm(env):
    simulated = env.client.post("/api/simulate")
    assert simulated.status_code == 200
    assert simulated.json["stored"] is True
    assert env.player.play_calls == 0
    assert env.app.alarm.is_active() is False


def test_dashboard_template_keeps_responder_actions_and_avoids_browser_audio():
    html = Path("templates/dashboard.html").read_bytes()
    assert b"Silence Alarm" in html
    assert b'id="silenceAlarm"' in html
    assert b"/api/alarm/silence" in html
    assert b"/api/alarm/status" in html
    assert b"Audible alarm active" in html
    assert b"Alarm silenced" in html
    assert b"Acknowledge" in html
    assert b"On my way" in html
    assert b"Resolve" in html
    assert b"alarmBtn" not in html
    assert b"Alarm on" not in html
    assert b"Alarm off" not in html
    assert b"speechSynthesis" not in html
    assert b"AudioContext" not in html


def test_alarm_wav_is_a_local_file():
    path = Path("static/audio/sos_alarm.wav")
    assert path.is_file()
    with wave.open(str(path)) as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() >= 8000
        assert handle.getnframes() > handle.getframerate() // 2
