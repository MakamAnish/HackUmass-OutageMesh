from ranging import DEFAULT_M, estimate_range_m


def test_missing_rtt_uses_default():
    assert estimate_range_m(None) == DEFAULT_M
    assert estimate_range_m("nope") == DEFAULT_M


def test_rtt_maps_to_indoor_meters():
    near = estimate_range_m(3)
    mid = estimate_range_m(20)
    far = estimate_range_m(80)
    assert 2 <= near < mid < far <= 45
    assert estimate_range_m(1000) == 45.0
