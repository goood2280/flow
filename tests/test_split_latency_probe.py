from scripts import check_split_server_latency as probe


def test_probe_reports_phases_without_identity_or_internal_metadata():
    payload = {"rows": [{"_param": "KNOB_X"}], "view_cache": {"hit": False},
               "runtime_profile": {"username": "private", "select_ms": 123.4,
                    "collect_ms": 8, "payload_cache_hit": False, "root_cache_hit": True,
                    "unaccounted_ms": float("nan"), "sql": "internal", "product": "internal"}}
    sample = probe.response_sample(payload, request_number=1, elapsed_ms=150, body_bytes=200)
    assert sample["ready"] is True
    assert sample["timings_ms"] == {"select_ms": 123.4, "collect_ms": 8}
    assert sample["cache_flags"]["root_cache_hit"] is True
    assert "private" not in str(sample) and "internal" not in str(sample)


def test_probe_does_not_count_empty_or_incomplete_tables_as_ready():
    for payload in ({"rows": []}, {"rows": [{}], "background_cache": {"queued": True}},
                    {"rows_compact": [{}], "runtime_profile": {"cache_incomplete": True}}):
        assert probe.response_sample(payload, request_number=1, elapsed_ms=1, body_bytes=20)["ready"] is False
    assert probe.response_sample({"rows_compact": [{}]}, request_number=1,
                                 elapsed_ms=1, body_bytes=20)["ready"] is True
