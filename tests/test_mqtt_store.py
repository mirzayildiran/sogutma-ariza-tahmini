import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from sogutma.mqtt_contract import normalize_telemetry
from sogutma.mqtt_store import SQLiteTelemetryStore, TelemetryConflict


def payload(ts="2026-10-03T12:05:00+03:00", site_id="S001", unit_id="A1", **overrides):
    message = {
        "schema": 1,
        "ts": ts,
        "site_id": site_id,
        "unit_id": unit_id,
        "gateway_id": "GW-0001",
        "interval_s": 300,
        "t_amb": 22.4,
        "t_room": 2.6,
        "p_suc_bar": {"mean": 3.9, "min": 3.6, "max": 4.2},
        "p_dis_bar": {"mean": 13.8, "min": 13.1, "max": 14.4},
        "i_comp_a": {"mean": 8.9, "max": 22.5},
        "pressure_ref": "absolute",
        "quality": {"p_suc": "ok"},
    }
    message.update(overrides)
    return message


def normalized(message):
    topic = f"sogutma/v1/{message['site_id']}/{message['unit_id']}/telemetry"
    return normalize_telemetry(topic, json.dumps(message))


def at(hour, minute):
    return datetime(2026, 10, 3, hour, minute, tzinfo=timezone.utc)


def test_persists_across_reopen_and_returns_canonical_api_rows(tmp_path):
    db = tmp_path / "telemetry.sqlite3"
    item = normalized(payload())
    store = SQLiteTelemetryStore(db)
    assert store.insert(item) == "inserted"
    store.close()

    with SQLiteTelemetryStore(db) as reopened:
        rows = reopened.api_rows("S001", "A1", at(9, 0), at(9, 10))

    assert len(rows) == 1
    assert rows[0]["unit_id"] == "A1"
    assert rows[0]["timestamp"] == "2026-10-03 09:05:00"


def test_qos_redelivery_is_idempotent_across_json_order_and_timezone(tmp_path):
    first = payload()
    equivalent = deepcopy(first)
    equivalent["ts"] = "2026-10-03T09:05:00Z"
    equivalent = dict(reversed(list(equivalent.items())))

    with SQLiteTelemetryStore(tmp_path / "telemetry.sqlite3") as store:
        assert store.insert(normalized(first)) == "inserted"
        assert store.insert(normalized(equivalent)) == "duplicate"
        assert len(store.query_window("S001", "A1", at(9, 0), at(9, 10))) == 1


def test_conflicting_redelivery_is_rejected_without_overwriting_original(tmp_path):
    original = payload()
    changed = payload(p_suc_bar={"mean": 3.9, "min": 3.5, "max": 4.2})

    with SQLiteTelemetryStore(tmp_path / "telemetry.sqlite3") as store:
        assert store.insert(normalized(original)) == "inserted"
        with pytest.raises(TelemetryConflict):
            store.insert(normalized(changed))
        [saved] = store.query_window("S001", "A1", at(9, 0), at(9, 10))

    assert saved.validated_payload["p_suc_bar"]["min"] == 3.6


def test_same_unit_and_timestamp_are_isolated_by_site(tmp_path):
    with SQLiteTelemetryStore(tmp_path / "telemetry.sqlite3") as store:
        assert store.insert(normalized(payload(site_id="S001"))) == "inserted"
        assert store.insert(normalized(payload(site_id="S002"))) == "inserted"
        assert len(store.query_window("S001", "A1", at(9, 0), at(9, 10))) == 1
        assert len(store.query_window("S002", "A1", at(9, 0), at(9, 10))) == 1
        assert store.api_rows("S001", "A1", at(9, 0), at(9, 10))[0]["unit_id"] == "A1"


def test_out_of_order_inserts_are_sorted_and_gaps_are_not_filled(tmp_path):
    with SQLiteTelemetryStore(tmp_path / "telemetry.sqlite3") as store:
        for minute in (10, 0, 20):
            sample = payload(ts=f"2026-10-03T09:{minute:02d}:00Z")
            assert store.insert(normalized(sample)) == "inserted"

        rows = store.api_rows("S001", "A1", at(9, 0), at(9, 20))

    assert [row["timestamp"] for row in rows] == [
        "2026-10-03 09:10:00",
        "2026-10-03 09:20:00",
    ]


def test_window_uses_start_exclusive_end_inclusive_and_site_unit_scope(tmp_path):
    with SQLiteTelemetryStore(tmp_path / "telemetry.sqlite3") as store:
        for minute in (0, 5, 10):
            store.insert(normalized(payload(ts=f"2026-10-03T09:{minute:02d}:00Z")))
        store.insert(normalized(payload(ts="2026-10-03T09:10:00Z", unit_id="A2")))

        rows = store.api_rows("S001", "A1", at(9, 0), at(9, 10))

    assert [row["timestamp"] for row in rows] == ["2026-10-03 09:05:00", "2026-10-03 09:10:00"]


def test_invalid_message_does_not_leave_partial_row(tmp_path):
    item = normalized(payload())
    invalid = dict(item.validated_payload)
    invalid["schema"] = 2
    bad_item = replace(item, validated_payload=invalid)

    with SQLiteTelemetryStore(tmp_path / "telemetry.sqlite3") as store:
        with pytest.raises(ValueError, match="geçersiz"):
            store.insert(bad_item)
        assert store.query_window("S001", "A1", at(9, 0), at(9, 10)) == []


def test_window_requires_timezone_and_positive_duration(tmp_path):
    with SQLiteTelemetryStore(tmp_path / "telemetry.sqlite3") as store:
        with pytest.raises(ValueError, match="saat dilimli"):
            store.query_window("S001", "A1", datetime(2026, 10, 3, 9), at(9, 10))
        with pytest.raises(ValueError, match="önce olmalı"):
            store.query_window("S001", "A1", at(9, 10), at(9, 0))


def test_window_is_bounded_to_ingest_span_limit(tmp_path):
    start = datetime(2025, 10, 2, 9, tzinfo=timezone.utc)
    end = datetime(2026, 10, 4, 9, tzinfo=timezone.utc)
    with SQLiteTelemetryStore(tmp_path / "telemetry.sqlite3") as store:
        with pytest.raises(ValueError, match="en fazla 366 gün"):
            store.query_window("S001", "A1", start, end)
