# Roadmap

Status: working demo prototype. Items marked done describe the current prototype scope as it exists in the repository's code and tests; they do not imply field validation or production readiness.

## Done: demo and integration

- [x] Physics-based simulator; cold room, freezer room and display cabinet; five equipment fault types.
- [x] Anomaly, fault-class and time-to-failure models; synthetic test fleet with separated unit groups.
- [x] CSV ingestion/validation, CLI prediction and hourly report.
- [x] Streamlit fleet, data analysis, ROI and notification settings / dry-run preview pages.
- [x] CLI email, Telegram and webhook delivery; real delivery must be enabled explicitly.
- [x] Sensor-fault simulation and a heuristic data-quality layer; integrated into the CSV, CLI, dashboard and API prediction paths.
- [x] FastAPI CSV/JSON prediction endpoints and an optional API key.
- [x] Docker image/Compose and GitHub Actions checks: pytest, Ruff and container smoke tests.

## Next: validation and data pipeline

- [x] Measure the fixed sensor-quality thresholds on additional synthetic profiles/seeds; do not present the results as field thresholds or field performance.
- [x] Produce per-class Brier/log-loss, conditional ETA error and a ±8 °C constant ambient-shift stress metric on the baseline synthetic test.
- [x] Evaluate raw class probabilities with reliability bins, Brier/log-loss and ETA MAE using a unit-clustered bootstrap on the held-out synthetic test units.
- [ ] Do not fit a calibrator or ETA prediction interval without separate calibration units; build a calibration set that is independent of the training/test units for such an interval.
- [ ] Evaluate simultaneous equipment faults (current sensor fault + equipment fault tested together), long-term seasonality and heat-wave scenarios.
- [x] Add a pure validation / canonical-field adapter for the MQTT v1 telemetry contract; broker, reconnection and persistent ingestion are out of scope.
- [x] Local SQLite staging prototype: site/unit/UTC window key, idempotent replay of the same payload, reject on conflict, no back-filling of missing ranges.
- [ ] MQTT producer/subscriber prototype; test time, unit, missing-value and reconnection behavior with a broker.
- [ ] Choose a production time-series storage approach based on pilot requirements, retention period and operating cost.

## Pilot and productization: waiting for field data and requirements

- [ ] Shadow-mode pilot with real unit/sensor data and service records; measure false alarms, misses and early-warning lead time.
- [ ] Compare equipment-, refrigerant- and unit-level baseline approaches in the pilot.
- [ ] Add chillers and other equipment types if pilot evidence and a support decision emerge.
- [ ] Design dashboard authentication / reverse proxy, tenant boundaries and a retention policy for production external access.
- [ ] Add reliable model artifact versioning, atomic deployment, prediction/quality metrics and alert observability.
- [ ] Prepare a cloud deployment suited to the pilot infrastructure, plus operations/runbook documentation.

## Working and validation rules

- Every code change runs `python -m pytest`, `ruff check .` and `git diff --check`.
- User-facing dashboard and CLI text is in Turkish; documentation is in English. Source code stays compatible with Python 3.9.
- Synthetic-data metrics are always labeled as synthetic; field performance is reported only from pilot measurements.
- CI runs tests/Ruff on Python 3.9 and 3.12 (the current support matrix), plus Docker image and API/dashboard health smoke checks.
