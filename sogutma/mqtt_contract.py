"""MQTT v1 telemetry contract validation and REST-row normalization.

This module deliberately has no broker client or persistence layer. It provides a
small, testable boundary for a future MQTT consumer to validate one telemetry
message and map it to the canonical measurement fields used by ``ingest``/API.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, confloat, field_validator

BAR_GAUGE_TO_ABSOLUTE = 1.013
TOPIC_PREFIX = "sogutma/v1"
MAX_PAYLOAD_BYTES = 64 * 1024
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
_FiniteNumber = confloat(allow_inf_nan=False)


class ContractError(ValueError):
    """An MQTT topic or payload does not satisfy the documented v1 contract."""


class PressureSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mean: Optional[_FiniteNumber]
    min: Optional[_FiniteNumber] = None
    max: Optional[_FiniteNumber] = None


class CurrentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mean: Optional[_FiniteNumber]
    max: Optional[_FiniteNumber] = None


class TelemetryV1(BaseModel):
    """Documented gateway telemetry envelope (one unit, one time bucket)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = Field(alias="schema")
    ts: datetime
    site_id: str = Field(min_length=1, max_length=64, pattern=_ID_PATTERN)
    unit_id: str = Field(min_length=1, max_length=64, pattern=_ID_PATTERN)
    gateway_id: str = Field(min_length=1, max_length=64, pattern=_ID_PATTERN)
    interval_s: StrictInt = Field(..., ge=300, le=300)
    setpoint_c: Optional[_FiniteNumber] = None
    t_amb: Optional[_FiniteNumber]
    t_room: Optional[_FiniteNumber]
    t_coil: Optional[_FiniteNumber] = None
    p_suc_bar: PressureSummary
    p_dis_bar: PressureSummary
    t_suction_line: Optional[_FiniteNumber] = None
    t_liquid_line: Optional[_FiniteNumber] = None
    t_dis: Optional[_FiniteNumber] = None
    i_comp_a: CurrentSummary
    i_fan_a: Optional[_FiniteNumber] = None
    vib_rms_mms: Optional[_FiniteNumber] = None
    comp_run_s: Optional[StrictInt] = Field(None, ge=0)
    comp_starts: Optional[StrictInt] = Field(None, ge=0)
    defrost_s: Optional[StrictInt] = Field(None, ge=0)
    door_open_s: Optional[StrictInt] = Field(None, ge=0)
    pressure_ref: Literal["absolute", "gauge"]
    refrigerant: Optional[str] = Field(None, min_length=1, max_length=32)
    quality: Dict[str, str] = Field(default_factory=dict)
    fw: Optional[str] = Field(None, max_length=64)

    @field_validator("ts")
    @classmethod
    def timestamp_must_have_offset(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("ts saat dilimi içermeli (UTC Z veya açık offset).")
        return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class NormalizedTelemetry:
    """Canonical API measurement plus provenance not represented in the API schema."""

    site_id: str
    gateway_id: str
    interval_s: int
    refrigerant: Optional[str]
    firmware: Optional[str]
    comp_starts: Optional[int]
    quality: Dict[str, str]
    measurement: Dict[str, object]


def _parse_topic(topic: str) -> Tuple[str, str]:
    parts = topic.split("/")
    if len(parts) != 5 or "/".join(parts[:2]) != TOPIC_PREFIX or parts[4] != "telemetry":
        raise ContractError("Topic sogutma/v1/{site_id}/{unit_id}/telemetry biçiminde olmalı.")
    site_id, unit_id = parts[2:4]
    if not re.fullmatch(_ID_PATTERN, site_id) or not re.fullmatch(_ID_PATTERN, unit_id):
        raise ContractError("Topic içindeki site_id ve unit_id geçersiz.")
    return site_id, unit_id


def normalize_telemetry(topic: str, payload: Union[bytes, str]) -> NormalizedTelemetry:
    """Validate one v1 telemetry message and map it to the REST measurement schema.

    ``quality`` and site/gateway metadata are preserved for a future durable
    ingestion layer; the existing model only consumes ``measurement``.
    """

    topic_site, topic_unit = _parse_topic(topic)
    try:
        if isinstance(payload, bytes):
            if len(payload) > MAX_PAYLOAD_BYTES:
                raise ContractError("MQTT payload 64 KiB sınırını aşıyor.")
            raw = payload.decode("utf-8")
        elif isinstance(payload, str):
            if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
                raise ContractError("MQTT payload 64 KiB sınırını aşıyor.")
            raw = payload
        else:
            raise ContractError("MQTT payload UTF-8 metin veya bytes olmalı.")
        parsed = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        message = TelemetryV1.model_validate(parsed)
    except ContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
        detail = str(exc)
        raise ContractError(f"MQTT telemetry v1 geçersiz: {detail}") from exc

    if message.site_id != topic_site or message.unit_id != topic_unit:
        raise ContractError("Payload site_id/unit_id değerleri topic ile eşleşmeli.")

    pressure_shift = BAR_GAUGE_TO_ABSOLUTE if message.pressure_ref == "gauge" else 0.0
    measurement: Dict[str, object] = {
        "timestamp": message.ts.replace(tzinfo=None).isoformat(sep=" "),
        "unit_id": message.unit_id,
        "t_amb": message.t_amb,
        "t_room": message.t_room,
        "p_suc": (
            None if message.p_suc_bar.mean is None else message.p_suc_bar.mean + pressure_shift
        ),
        "p_dis": (
            None if message.p_dis_bar.mean is None else message.p_dis_bar.mean + pressure_shift
        ),
        "i_comp": message.i_comp_a.mean,
        "t_coil": message.t_coil,
        "t_suc": message.t_suction_line,
        "t_liq": message.t_liquid_line,
        "t_dis": message.t_dis,
        "i_fan": message.i_fan_a,
        "vib": message.vib_rms_mms,
        "setpoint": message.setpoint_c,
        "comp_on": None if message.comp_run_s is None else message.comp_run_s > 0,
        "defrost": None if message.defrost_s is None else message.defrost_s > 0,
        "door_open": None if message.door_open_s is None else message.door_open_s > 0,
    }
    return NormalizedTelemetry(
        site_id=message.site_id,
        gateway_id=message.gateway_id,
        interval_s=message.interval_s,
        refrigerant=message.refrigerant,
        firmware=message.fw,
        comp_starts=message.comp_starts,
        quality=dict(message.quality),
        measurement=measurement,
    )
