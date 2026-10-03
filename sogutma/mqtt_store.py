"""Local SQLite staging store for validated MQTT v1 telemetry.

This single-process prototype is not a replacement for a production time-series
database. It preserves the full validated contract envelope while exposing
site-scoped, ordered measurements to the existing stateless REST API.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Union

from .mqtt_contract import NormalizedTelemetry, normalize_telemetry

SCHEMA_VERSION = 1
MAX_WINDOW_DAYS = 366
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class TelemetryConflict(ValueError):
    """The same site/unit/window key arrived with different validated contents."""


def _utc_key(value: datetime) -> str:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Pencere sınırları saat dilimli datetime olmalı.")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _validate_id(value: str, label: str) -> None:
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError(f"{label} geçersiz.")


class SQLiteTelemetryStore:
    """Durable local staging with idempotent duplicate handling and no gap filling."""

    def __init__(self, path: Union[str, Path]):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, timeout=5.0, check_same_thread=False)
        self._connection.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
        self._initialize()

    def _initialize(self) -> None:
        with self._connection:
            version = self._connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"SQLite telemetri şema sürümü desteklenmiyor: {version} > {SCHEMA_VERSION}"
                )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS telemetry (
                    site_id TEXT NOT NULL,
                    unit_id TEXT NOT NULL,
                    ts_utc TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (site_id, unit_id, ts_utc)
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS telemetry_site_unit_ts "
                "ON telemetry (site_id, unit_id, ts_utc)"
            )
            self._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def insert(self, item: NormalizedTelemetry) -> str:
        """Insert a message; return ``inserted`` or ``duplicate``; never overwrite."""
        if not isinstance(item, NormalizedTelemetry):
            raise TypeError("insert() için normalize edilmiş MQTT telemetry gerekir.")
        _validate_id(item.site_id, "site_id")
        unit_id = item.measurement["unit_id"]
        _validate_id(unit_id, "unit_id")
        topic = f"sogutma/v1/{item.site_id}/{unit_id}/telemetry"
        try:
            serialized = json.dumps(
                item.validated_payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            canonical = normalize_telemetry(topic, serialized)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Normalize edilmiş telemetry geçersiz: {exc}") from exc

        timestamp = datetime.fromisoformat(canonical.validated_payload["ts"].replace("Z", "+00:00"))
        ts_utc = _utc_key(timestamp)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO telemetry (site_id, unit_id, ts_utc, payload_json) "
                "VALUES (?, ?, ?, ?)",
                (canonical.site_id, unit_id, ts_utc, serialized),
            )
            if cursor.rowcount == 1:
                return "inserted"
            row = self._connection.execute(
                "SELECT payload_json FROM telemetry WHERE site_id = ? AND unit_id = ? AND ts_utc = ?",
                (canonical.site_id, unit_id, ts_utc),
            ).fetchone()
            if row is None:
                raise RuntimeError("Telemetri yazımı tamamlanamadı.")
            if row[0] == serialized:
                return "duplicate"
            raise TelemetryConflict(
                f"{canonical.site_id}/{unit_id}/{ts_utc} penceresinde farklı payload zaten kayıtlı."
            )

    def query_window(
        self,
        site_id: str,
        unit_id: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> List[NormalizedTelemetry]:
        """Return actual samples in the half-open telemetry window ``(start, end]``.

        Missing windows remain missing. Callers may pass the resulting one-unit,
        one-site ``measurement`` rows to the REST API, which performs its own
        minimum-duration, sample-gap, and sensor validation.
        """
        _validate_id(site_id, "site_id")
        _validate_id(unit_id, "unit_id")
        start_key, end_key = _utc_key(start_utc), _utc_key(end_utc)
        if start_key >= end_key:
            raise ValueError("Pencere başlangıcı bitişten önce olmalı.")
        if end_utc.astimezone(timezone.utc) - start_utc.astimezone(timezone.utc) > timedelta(
            days=MAX_WINDOW_DAYS
        ):
            raise ValueError(f"Sorgu penceresi en fazla {MAX_WINDOW_DAYS} gün olabilir.")

        with self._lock:
            rows = self._connection.execute(
                "SELECT payload_json FROM telemetry "
                "WHERE site_id = ? AND unit_id = ? AND ts_utc > ? AND ts_utc <= ? "
                "ORDER BY ts_utc ASC",
                (site_id, unit_id, start_key, end_key),
            ).fetchall()
        topic = f"sogutma/v1/{site_id}/{unit_id}/telemetry"
        return [normalize_telemetry(topic, row[0]) for row in rows]

    def api_rows(
        self,
        site_id: str,
        unit_id: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> List[dict]:
        """Return rows for one site/unit only; API itself has no tenant/site field."""
        return [
            item.measurement
            for item in self.query_window(site_id, unit_id, start_utc, end_utc)
        ]

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "SQLiteTelemetryStore":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
