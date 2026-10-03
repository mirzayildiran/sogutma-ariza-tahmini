import json

import pytest

from sogutma.api import Olcum
from sogutma.mqtt_contract import ContractError, normalize_telemetry


def telemetry(**overrides):
    message = {
        "schema": 1,
        "ts": "2026-10-03T12:05:00+03:00",
        "site_id": "S001",
        "unit_id": "A1",
        "gateway_id": "GW-0001",
        "interval_s": 300,
        "setpoint_c": 2.0,
        "t_amb": 22.4,
        "t_room": 2.6,
        "t_coil": -4.1,
        "p_suc_bar": {"mean": 3.9, "min": 3.6, "max": 4.2},
        "p_dis_bar": {"mean": 13.8, "min": 13.1, "max": 14.4},
        "t_suction_line": 6.5,
        "t_liquid_line": 29.8,
        "t_dis": 71.2,
        "i_comp_a": {"mean": 8.9, "max": 22.5},
        "i_fan_a": 1.2,
        "vib_rms_mms": 1.6,
        "comp_run_s": 210,
        "comp_starts": 1,
        "defrost_s": 0,
        "door_open_s": 14,
        "pressure_ref": "absolute",
        "refrigerant": "R404A",
        "quality": {"p_suc": "ok", "vib": "ok"},
        "fw": "0.1.0",
    }
    message.update(overrides)
    return message


TOPIC = "sogutma/v1/S001/A1/telemetry"


def test_absolute_telemetry_maps_to_canonical_measurement_and_keeps_metadata():
    result = normalize_telemetry(TOPIC, json.dumps(telemetry()).encode())

    assert result.site_id == "S001" and result.gateway_id == "GW-0001"
    assert result.measurement["timestamp"] == "2026-10-03 09:05:00"
    assert result.measurement["p_suc"] == 3.9 and result.measurement["p_dis"] == 13.8
    assert result.measurement["t_suc"] == 6.5 and result.measurement["t_liq"] == 29.8
    assert result.measurement["comp_on"] is True
    assert result.measurement["defrost"] is False and result.measurement["door_open"] is True
    assert result.quality == {"p_suc": "ok", "vib": "ok"}
    assert result.comp_starts == 1 and result.firmware == "0.1.0"
    parsed_api_row = Olcum.model_validate(result.measurement)
    assert parsed_api_row.unit_id == "A1" and parsed_api_row.timestamp.utcoffset() is None


def test_gauge_pressure_is_normalized_to_absolute_and_null_is_preserved():
    message = telemetry(
        pressure_ref="gauge",
        p_suc_bar={"mean": 0.0, "min": 0.0, "max": 0.1},
        p_dis_bar={"mean": None, "min": None, "max": None},
        comp_run_s=None,
        defrost_s=None,
        door_open_s=None,
    )

    result = normalize_telemetry(TOPIC, json.dumps(message))
    assert result.measurement["p_suc"] == pytest.approx(1.013)
    assert result.measurement["p_dis"] is None
    assert result.measurement["comp_on"] is None
    assert result.measurement["defrost"] is None
    assert result.measurement["door_open"] is None


@pytest.mark.parametrize(
    ("topic", "message"),
    [
        ("sogutma/v1/S001/A1/events", telemetry()),
        ("sogutma/v1/S002/A1/telemetry", telemetry()),
        (TOPIC, telemetry(schema=2)),
        (TOPIC, telemetry(interval_s=60)),
        (TOPIC, telemetry(ts="2026-10-03T09:05:00")),
        (TOPIC, telemetry(pressure_ref="relative")),
        (TOPIC, telemetry(p_suc_bar={"mean": float("nan")})),
        (TOPIC, telemetry(unexpected=True)),
    ],
)
def test_invalid_topic_or_payload_is_rejected(topic, message):
    with pytest.raises(ContractError):
        normalize_telemetry(topic, json.dumps(message))


def test_missing_required_measurement_field_is_rejected():
    message = telemetry()
    del message["t_room"]

    with pytest.raises(ContractError):
        normalize_telemetry(TOPIC, json.dumps(message))


def test_oversized_payload_is_rejected_before_json_parsing():
    with pytest.raises(ContractError, match="64 KiB"):
        normalize_telemetry(TOPIC, b" " * (64 * 1024 + 1))
