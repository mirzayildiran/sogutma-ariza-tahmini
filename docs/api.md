# REST API and Docker

The analysis pipeline used by the dashboard and `predict.py` (CSV reading/validation → hourly features → model) is
also served as an HTTP service. An IoT gateway, maintenance software or any other system can send measurements and
receive the health score, likely fault and time to failure for each unit.

> [!IMPORTANT]
> The model was trained on **synthetic (simulator) data**; real field accuracy has not been validated. The API states
> this in every response with the `sentetik_uyari` (synthetic warning) field. The output is an early indicator; it does
> not replace a pressure switch, an alarm device or an official temperature record. The service is stateless (there is
> no database): you send the data to be analyzed with every call.

> [!NOTE]
> Endpoint paths, JSON field names and error messages are in Turkish. Their meanings are given in this document.

## Contents

1. [Running](#1-running)
2. [Endpoints](#2-endpoints)
3. [Authentication](#3-authentication)
4. [Errors](#4-errors)
5. [Docker and docker compose](#5-docker-and-docker-compose)
6. [Sending hourly data from a gateway](#6-sending-hourly-data-from-a-gateway)
7. [Limits and security notes](#7-limits-and-security-notes)

## 1. Running

```bash
pip install -r requirements-api.txt     # also includes the dashboard dependencies
python train.py                          # once: trains the model (models/predictor.joblib)
uvicorn api:app --port 8000              # access from this machine only
# to expose it to other devices on the network: --host 0.0.0.0 (set an API key first, see §3)
```

Interactive docs (OpenAPI): <http://localhost:8000/docs>. When the model file changes (e.g. when `python train.py`
is run again), the service loads the new model without a restart.

| Environment variable | Meaning | Default |
|---|---|---|
| `SOGUTMA_ROOT` | Parent directory of the `models/` and `data/` folders (the dashboard uses the same one) | repository root |
| `SOGUTMA_API_ANAHTARI` | If set, all endpoints except `/saglik` require the `X-API-Anahtari` header | unset (open; a warning is logged at startup) |
| `SOGUTMA_MAKS_YUKLEME_MB` | Request body limit (MB) | 20 |

## 2. Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/saglik` (health) | Liveness: is the model loaded, model version, uptime. No authentication required. **503** if the model is unavailable. |
| GET | `/model` | Model version, fault types, equipment types and a summary of `models/metrics.json` (**synthetic** measurements). |
| POST | `/tahmin/csv` (predict from CSV) | Upload a CSV file (`multipart/form-data`, field name `dosya`) → prediction. |
| POST | `/tahmin/olcumler` (predict from measurements) | Send a JSON list of measurements → prediction. |

### GET /saglik

```bash
curl http://localhost:8000/saglik
```
```json
{"durum": "ok", "model_yuklu": true, "model_surumu": 2, "calisma_suresi_sn": 3.4, "kimlik_dogrulama": false}
```

(`durum` = status, `model_yuklu` = model loaded, `model_surumu` = model version, `calisma_suresi_sn` = uptime in
seconds, `kimlik_dogrulama` = authentication enabled.)

If the model is missing or outdated, HTTP 503 is returned and `durum` is `model_yok` (no model), `model_eski`
(outdated model) or `model_okunamadi` (model unreadable).

### GET /model

```bash
curl http://localhost:8000/model
```
The response always contains a `sentetik: true` field and an `uyari` (warning) text. `metrikler` (metrics) contains
hourly accuracy, macro F1, the share of faults caught before failure, the median early-warning lead time (days), the
number of units with a false alarm and a breakdown by equipment type; all of it comes from the synthetic test fleet.
In addition, `multiclass_brier` and `log_loss` measure the raw, **uncalibrated** class probabilities. `eta_mae_saat`
and `eta_p90_mutlak_hata_saat` are the conditional ETA errors (in hours) for test hours where `pred_fault` is a
non-normal class and the true time to failure is positive; they do not require the correct fault type to be predicted.
`eta_dogru_tur_mae_saat` and `eta_dogru_tur_p90_mutlak_hata_saat` are computed only when the type prediction matches the
true label. Consecutive hours are not independent samples. These synthetic metrics are not evidence of field
performance or probability calibration. `ortam_kaymasi_stresi` (ambient-shift stress) gives the stress check in which a
constant ambient offset of −8 °C and +8 °C is applied to the same synthetic test fleet; it is not a real seasonal or
field test. The training output `models/metrics.json` also contains a per-class 10-bin reliability table and
unit-clustered bootstrap intervals for Brier/log-loss/ETA MAE. The `/model` summary does not return these detailed tables;
the bootstrap intervals are not a confidence/prediction interval for a single ETA estimate.

### POST /tahmin/csv

The CSV format is the same as in the [Data format](data-format.md) document (Turkish Excel exports included).

```bash
curl -F dosya=@examples/ornek_veri.csv "http://localhost:8000/tahmin/csv"
curl -F dosya=@freezer.csv "http://localhost:8000/tahmin/csv?tip=dondurucu&setpoint=-20&saatlik=true"
```

Query parameters:

| Parameter | Meaning |
|---|---|
| `setpoint` | Thermostat setpoint (°C) if the CSV has no setpoint column; if omitted, the median room temperature |
| `gauge` | If `true`, pressures are treated as gauge pressures and converted to absolute by adding 1.013 bar |
| `tip` | Equipment type if the CSV has no `tip` column: `soguk_oda` (cold room, default), `dondurucu` (freezer), `market_dolabi` (display cabinet) |
| `saatlik` | If `true`, the hourly prediction series is returned as well |

Response (abbreviated; sample CSV):

```json
{
  "model_surumu": 2,
  "sentetik_uyari": "Model sentetik (simülatör) veriyle eğitildi ...",
  "uniteler": [
    {
      "unit_id": "U1",
      "zaman": "2026-01-10T23:00:00",
      "saat_sayisi": 228,
      "saglik": 13.6,
      "durum": "Kritik",
      "ariza": "gaz_kacagi",
      "ariza_adi": "Soğutucu gaz kaçağı",
      "guven": 1.0,
      "kalan_saat": 39.0,
      "olasiliklar": {"normal": 0.0, "gaz_kacagi": 1.0, "kondenser_kirlenmesi": 0.0, "...": 0.0},
      "sapmalar": [
        {"sinyal": "Kızgınlık / superheat (K)", "deger": 19.114, "normal": 5.997, "sapma_sigma": 144.84}
      ],
      "oneri": "Kaçak testi yapın (elektronik dedektör / köpük), kaçağı giderin, ...",
      "uyari_baslangic": "2026-01-06T19:00:00",
      "uyari_saat": 101,
      "sensor_sorunu": false,
      "sensor_notu": "",
      "sensor_sorunlari": []
    }
  ],
  "rapor": {
    "bilgiler": ["unit_id sütunu yok; tek ünite varsayıldı ('U1')."],
    "uyarilar": [],
    "turetilen": {"tip": "sütun yok; soğuk oda (soguk_oda) varsayıldı"},
    "eksik_sensorler": [],
    "hesaplanamayan_oznitelikler": [],
    "yinelenen_satir": 0,
    "uniteler": {"U1": {"bas": "2026-01-01T00:00:00", "bit": "2026-01-10T23:55:00", "saat": 240.0,
                        "kapsam": 1.0, "ham_satir": 2880, "orneklem_dk": 5.0}}
  },
  "saatlik": null
}
```

Fields:

- `uniteler`: one entry per unit. `zaman` = time of the latest hour, `saat_sayisi` = number of hours analyzed.
- `durum` (state): `Normal`, `İzlemede` (Watch) or `Kritik` (Critical); `saglik` (health) is the 0–100 score.
- `ariza` / `ariza_adi`: predicted fault code and its Turkish name (including `normal`). `guven` (confidence) is 0–1.
  `olasiliklar` holds the probability of each class.
- `kalan_saat`: hours left until failure, a rough estimate; `null` if `durum` is Normal.
- `sapmalar` (deviations): the signals that deviate most from normal operation of the same type (explanation);
  `sapma_sigma` is a signed z-score. `sinyal` = signal, `deger` = value, `normal` = normal value.
- `oneri`: maintenance recommendation (Turkish text).
- `uyari_baslangic` / `uyari_saat`: start and length of the uninterrupted non-Normal period counting back from the end
  (0 means no warning).
- `sensor_sorunu` / `sensor_notu`: a separate warning if there is a sensor issue in the last hour; independent of the
  equipment-fault diagnosis.
- `sensor_sorunlari`: summary over the analysis range by sensor and reason code (`sensor`, `neden` = reason,
  `ilk` = first, `son` = last, `saat` = hours).
- The sensor check is heuristic and tuned to simulator conditions; it has not been validated as a field alarm. For the
  thresholds, see the limits in [Data format](data-format.md#51-sensor-health-data-quality-checks).
- `rapor` (report): the data-check report. `bilgiler` = info messages, `uyarilar` reports corrected/dropped data,
  `turetilen` assumed/derived fields, and `eksik_sensorler` (missing sensors) and `hesaplanamayan_oznitelikler`
  (features that could not be computed) report gaps that reduce accuracy. Always read it with real data.
- `saatlik` (only with `saatlik=true`): for each unit and hour, the prediction plus the `sensor_sorunu` and
  `sensor_notu` fields.
- Timestamps in the API are UTC. A value without a time zone is treated as UTC; JSON times with an offset are converted
  to UTC. Do not mix values with and without a time zone in a single JSON request. The hourly/unit response uses
  UTC-naive ISO format. In CSV and CLI files, too, a time without a time zone is treated as UTC; if you send local time,
  add an explicit UTC offset.

### POST /tahmin/olcumler

A gateway sends its latest measurements as JSON. The response format is the same as `/tahmin/csv`.

Each record carries the canonical fields of the CSV schema: `timestamp`, `t_amb`, `t_room`, `p_suc`, `p_dis` and
`i_comp` are required (the value may be `null` if the sensor could not be read, but the field must be present);
`unit_id`, `tip`, `t_coil`, `sh`, `sc`, `t_suc`, `t_liq`, `i_fan`, `vib`, `t_dis`, `comp_on`, `defrost`, `door_open`
and `setpoint` are optional. For units and the effect of missing fields, see [Data format](data-format.md).
`setpoint`, `gauge`, `tip` and `saatlik` are top-level fields in the request body; the measurements go in `olcumler`.

**At least one day (24 hours) of valid data** is required (per unit); 3+ days is recommended for reliable results. A
request accepts at most 500 units, 366 days per unit and 1,000,000 aligned five-minute points in total. Because the
service is stateless, send the whole window with every call (288 records per unit per day at 5-minute sampling). The
JSON endpoint also accepts at most 200,000 raw measurement records; the HTTP body on both endpoints is limited to
20 MB by default (`SOGUTMA_MAKS_YUKLEME_MB`).

```bash
curl -X POST http://localhost:8000/tahmin/olcumler \
  -H "Content-Type: application/json" \
  -d @request.json
```
```json
{
  "tip": "dondurucu",
  "setpoint": -20,
  "saatlik": false,
  "olcumler": [
    {"timestamp": "2026-01-05T14:35:00Z", "unit_id": "D1", "t_amb": 24.1, "t_room": -19.6,
     "p_suc": 2.05, "p_dis": 13.8, "i_comp": 5.2, "i_fan": 0.9, "vib": 1.8,
     "comp_on": true, "defrost": false},
    {"timestamp": "2026-01-05T14:40:00Z", "unit_id": "D1", "t_amb": 24.1, "t_room": -19.5,
     "p_suc": 2.04, "p_dis": 13.9, "i_comp": 5.3, "i_fan": 0.9, "vib": 1.8,
     "comp_on": true, "defrost": false}
  ]
}
```
(A real request must contain at least 24 hours of records; the two records above only illustrate the format and return
**422**.)

## 3. Authentication

If the `SOGUTMA_API_ANAHTARI` environment variable is set, all endpoints except `/saglik` require the `X-API-Anahtari`
(API key) header (**401** if it is missing or wrong). The key is not stored in the code or the repository; it is
provided through the environment variable:

```bash
export SOGUTMA_API_ANAHTARI="$(openssl rand -hex 24)"
uvicorn api:app --port 8000
curl -H "X-API-Anahtari: $SOGUTMA_API_ANAHTARI" http://localhost:8000/model
```

If it is not set, the API is open to everyone and a warning is logged at startup. Set a key on every installation that
will be exposed to a network, and put a TLS-terminating reverse proxy (nginx, Caddy, a cloud load balancer) in front of
the service; the API itself does not serve HTTPS.

## 4. Errors

Error responses have a single format:

```json
{"hata": "veri_gecersiz", "mesaj": "Veri kullanılamıyor; ayrıntılar için 'ayrintilar' alanına bakın.",
 "ayrintilar": ["Zorunlu sütun(lar) eksik: t_amb (Dış ortam sıcaklığı) — kabul edilen adlar: ..."]}
```

(`hata` = error code, `mesaj` = message, `ayrintilar` = details. The example says: "Data cannot be used; see the
'ayrintilar' field for details" and "Required column(s) missing: t_amb (ambient temperature) — accepted names: ...".)

| Code | `hata` | When |
|---|---|---|
| 401 | `yetkisiz` (unauthorized) | API key missing or wrong |
| 413 | `cok_buyuk` (too large) | The request or file exceeds the limit (20 MB by default) |
| 422 | `veri_gecersiz` (invalid data) | The data failed ingest validation (required column missing, unreadable timestamp, unknown `tip`, less than 24 hours of data per unit, ...) |
| 422 | `yetersiz_veri` (insufficient data) | The file is valid but too short or too gappy to produce hourly features |
| 422 | `istek_gecersiz` (invalid request) | The request body/parameters are invalid (e.g. `olcumler[3].t_room: sayı bekleniyor`, "number expected") |
| 503 | `model_yok`, `model_eski`, `model_okunamadi` | The model file is missing, outdated or unreadable: run `python train.py` |
| 500 | `sunucu_hatasi` (server error) | Unexpected error; details are only in the server log |

## 5. Docker and docker compose

The repository has a single `Dockerfile` and a `docker-compose.yml` with two services:

| Service | Command | Port | Health check |
|---|---|---|---|
| `api` | `uvicorn api:app` | 8000 | `GET /saglik` |
| `panel` (dashboard) | `streamlit run app.py` | 8501 | `GET /_stcore/health` |

```bash
docker compose up --build                     # dashboard: http://localhost:8501   API: http://localhost:8000/docs
SOGUTMA_API_ANAHTARI="$(openssl rand -hex 24)" docker compose up -d --build   # with a key
docker compose ps                             # health status
docker compose down                           # stop (volumes are kept)
docker compose down -v                        # also delete the volumes (resets the model to the one in the image)
```

Design decisions:

- **The model is trained while the image is built** (`RUN python train.py`, synthetic data): the image is
  self-contained, the first request does not wait for training, and no internet access is needed on first start.
  The cost is a longer build. If the model version changes (`MODEL_VERSION`), rebuild the image with
  `docker compose build`.
- The image runs as a **non-root** user (`app`, uid 10001); the pip cache is not included in the image.
- Both services use the same image and share the named volumes `veri` (data, `/app/data`) and `modeller` (models,
  `/app/models`). The volumes are filled with the image's contents on first use. If the dashboard retrains the model,
  the API loads the new model without a restart. After the image is updated, old volumes keep the old model: if the
  API returns 503, reset the volumes with `docker compose down -v`.
- Ports are bound to `127.0.0.1` only. To expose them to the network, remove the prefixes in `docker-compose.yml` and
  always set `SOGUTMA_API_ANAHTARI`; the dashboard itself has no authentication.
- The health checks do not need `curl` (it is not in the slim image); they use Python.

## 6. Sending hourly data from a gateway

In the repository, `sogutma/mqtt_contract.py` validates the MQTT v1 `telemetry` message; `sogutma/mqtt_store.py` writes
the validated full envelope to a local SQLite staging store and reads time-window rows for a single site/unit.
`normalize_telemetry(topic, payload)` maps a single 300-second message to a `/tahmin/olcumler` row and keeps the
provenance fields that the REST API does not have. A broker client, reconnection, gateway spooling and production storage
are not implemented. For the contract, conflict and missing-range behavior, see
[Field architecture, MQTT v1](field-architecture.md#6-mqtt-topic-structure-and-data-format).

A local consumer can use validation and staging separately as follows (a broker callback is not part of this
prototype):

```python
from datetime import datetime, timezone
from sogutma.mqtt_contract import normalize_telemetry
from sogutma.mqtt_store import SQLiteTelemetryStore

with SQLiteTelemetryStore("data/telemetry.sqlite3") as store:
    sample = normalize_telemetry(topic, payload_bytes)
    store.insert(sample)  # returns "duplicate" on a QoS 1 redelivery; different content under the same key raises an error
    rows = store.api_rows(
        "S001", "A1",
        datetime(2026, 10, 2, 9, tzinfo=timezone.utc),
        datetime(2026, 10, 3, 9, tzinfo=timezone.utc),
    )
```

`rows` belongs to a single site/unit; it can be POSTed as `{"olcumler": rows}`. The API still requires at least 24 hours
of valid data. Long gaps are not filled with synthetic rows; the API ingest report evaluates the gaps.

Every hour, the gateway (or a local script) sends the last 3 days of 5-minute measurements and acts on the warning in
the response. The example below uses only the standard library; `latest_measurements()` is a function **you need to
write** that reads records from your storage layer (SQLite, InfluxDB, ...).

```python
"""send_hourly.py: sends the last 3 days of measurements to the API (cron: 5 * * * *)."""
import json
import os
import urllib.error
import urllib.request

API = os.environ.get("SOGUTMA_API_ADRESI", "http://localhost:8000")
API_KEY = os.environ.get("SOGUTMA_API_ANAHTARI", "")


def latest_measurements(days=3):
    """[{"timestamp": "2026-01-05T14:35:00", "unit_id": "D1", "t_amb": ..., "t_room": ..., ...}, ...]"""
    raise NotImplementedError("Read from your own data source")


def get_prediction(measurements, equipment_type="soguk_oda"):
    body = json.dumps({"olcumler": measurements, "tip": equipment_type}).encode("utf-8")
    request = urllib.request.Request(
        f"{API}/tahmin/olcumler",
        data=body,
        headers={"Content-Type": "application/json", "X-API-Anahtari": API_KEY},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:  # 4xx/5xx: print the error body
        raise SystemExit(f"API error {e.code}: {e.read().decode('utf-8')}")


if __name__ == "__main__":
    response = get_prediction(latest_measurements())
    for u in response["uniteler"]:
        print(f"{u['unit_id']}: {u['durum']} (health {u['saglik']:.0f}), {u['ariza_adi']}")
        if u["durum"] != "Normal":
            print(f"  Recommendation: {u['oneri']}")  # you can send a notification here
```

For notification (email / Telegram / webhook) rules, see [Notifications](notifications.md); for the gateway and MQTT
design, see [Field Architecture](field-architecture.md).

## 7. Limits and security notes

- The upload limit is applied to the Content-Length header and to the body as it is read; for real protection, also set
  a body size limit on the reverse proxy you put in front of the service.
- The service runs the analysis synchronously inside the request; large files (a few hundred thousand rows) can take
  seconds. For many clients, run several worker processes (`uvicorn api:app --workers 4`).
- The model file is read with `joblib` (pickle-based): put only models you trained yourself under `models/`, and do not
  load models from sources you do not trust.
- The API key is only a simple access gate (a single shared key); multi-tenant use, per-user permissions and rate
  limiting are not available in this version (the "multi-tenant support" item in the README roadmap).
