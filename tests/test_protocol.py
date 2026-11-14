import pytest

from protocol import MAX_PAYLOAD, MAX_TEXT, decode, encode, make_zone, next_id, sanitize


def test_round_trip():
    payload = encode("A47", "DUB-3-F14", "MED", "needs fridge for insulin")
    assert payload == "A47|DUB-3-F14|MED|needs fridge for insulin"
    msg = decode(payload)
    assert msg["id"] == "A47"
    assert (msg["building"], msg["floor"], msg["room"]) == ("DUB", "3", "F14")
    assert msg["category"] == "MED"
    assert msg["text"] == "needs fridge for insulin"


def test_sanitize_strips_separators_quotes_commas_and_non_ascii():
    assert sanitize('help | "now", please\nthanks 🚑') == "help now please thanks"


def test_sanitize_truncates():
    assert len(sanitize("x" * 200)) == MAX_TEXT


def test_injected_pipe_cannot_add_fields():
    payload = encode("A1", "DUB-3-X", "OTHER", "hi|LOW|evil")
    assert len(payload.split("|")) == 4


def test_make_zone():
    assert make_zone("dub", "3", "f14") == "DUB-3-F14"
    assert make_zone("DUB", "3", None) == "DUB-3-X"
    assert decode(encode("A1", make_zone("DUB", "3", ""), "SAFE", "ok"))["room"] is None


@pytest.mark.parametrize(
    "args",
    [
        ("47", "DUB-3-F14", "MED", "x"),
        ("A47", "bad zone", "MED", "x"),
        ("A47", "DUB-3-F14", "URGENT", "x"),
    ],
)
def test_encode_rejects_bad_fields(args):
    with pytest.raises(ValueError):
        encode(*args)


@pytest.mark.parametrize("payload", ["", "A1|DUB-3-X|MED", "A1|DUB-3-X|NOPE|x", "garbage"])
def test_decode_rejects_bad_payloads(payload):
    with pytest.raises(ValueError):
        decode(payload)


def test_max_payload_fits():
    payload = encode("A99999", "ABCD-123-ABCDE", "WATER", "y" * 500)
    assert len(payload) <= MAX_PAYLOAD


def test_next_id_persists(tmp_path):
    counter = tmp_path / "counter.txt"
    assert next_id("a", counter) == "A1"
    assert next_id("A", counter) == "A2"
