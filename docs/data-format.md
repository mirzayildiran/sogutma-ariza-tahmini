# Using Your Own Data: Data Format

You can get predictions from your own sensor records by uploading a CSV on the **Analyze Your Own Data** page of the
dashboard (`streamlit run app.py`), or from the command line without the dashboard:

```bash
python train.py                      # once: trains the model (models/predictor.joblib)
python predict.py data.csv           # prediction + Turkish summary + hourly report CSV
python predict.py data.csv --setpoint 2.0 --gauge -o report.csv
python predict.py freezer.csv --tip dondurucu --setpoint -20   # equipment type: dondurucu (freezer)
```

A ready-made sample is included: `python predict.py examples/ornek_veri.csv`
(10 days, Turkish Excel format; a room that develops a refrigerant leak from day 5 onwards.
To regenerate it: `python examples/ornek_veri_uret.py`).

> [!IMPORTANT]
> The model was trained on **R404A** refrigerant and simulator data. Results for another refrigerant, a unit type the
> simulator does not cover (chillers, etc.) or real field noise have **not been validated**; use the output as an early
> indicator, not as a basis for decisions.
> The simulator knows three equipment types: `soguk_oda` (cold room, default), `dondurucu` (freezer room) and
> `market_dolabi` (display cabinet). Give the type **correctly** with `--tip` or a `tip` column; a wrong type (e.g. treating
> a freezer as a cold room) makes normal operation look like a fault.

## 1. Columns

The first row of the CSV must be the header. Column order does not matter; headers are insensitive to case,
Turkish characters and a unit suffix in parentheses (`Oda Sıcaklığı (°C)`).
Unrecognized columns are ignored.

### Required

| Column (canonical name) | Unit | Description | Accepted header examples |
|---|---|---|---|
| `timestamp` | date-time | Measurement time | `Tarih Saat`, `Zaman`, `timestamp`, `datetime`; or separate `Tarih` (date) + `Saat` (time) columns |
| `t_amb` | °C | Ambient (condenser air) temperature | `Dış Ortam Sıcaklığı`, `ambient_temp`, `dis_sicaklik` |
| `t_room` | °C | Room (cabinet) air temperature | `Oda Sıcaklığı`, `room_temp`, `Kabin Sıcaklığı` |
| `p_suc` | bar (absolute) | Suction (low-side) pressure | `Emme Basıncı`, `Alçak Basınç`, `suction_pressure`, `LP` |
| `p_dis` | bar (absolute) | Discharge (high-side) pressure | `Basma Basıncı`, `Yüksek Basınç`, `discharge_pressure`, `HP` |
| `i_comp` | A | Compressor current | `Kompresör Akımı`, `comp_current`, `Akım` |

### Optional

| Column | Unit | What happens if it is missing? |
|---|---|---|
| `unit_id` | text | A single unit is assumed (`U1`). Several units can share one file (`Ünite`, `Oda No`, `Cihaz`). |
| `setpoint` | °C | The `--setpoint` value is used; if that is missing too, the median room temperature. |
| `tip` | text | Equipment type: `soguk_oda`, `dondurucu`, `market_dolabi` (spellings such as `Dondurucu`, `freezer`, `vitrin` are also accepted). One value per unit; if missing, the `--tip` value, otherwise `soguk_oda`. A warning is shown if the room temperature is below −10 °C and no type is given. |
| `comp_on` | 0/1 | Derived from the threshold `i_comp > 0.5 A`. |
| `defrost` | 0/1 | Assumed to be always 0; the coil temperature signal during defrost cannot be used. |
| `door_open` | 0/1 | Not used by the model. |
| `t_coil` | °C | Evaporator coil / defrost sensor. If missing, the defrost peak temperature signal cannot be used. |
| `sh` | K | Superheat. If missing, derived from `t_suc` below; if that is missing too, it stays empty. |
| `sc` | K | Subcooling. If missing, derived from `t_liq`; if that is missing too, it stays empty. |
| `t_suc` | °C | Suction line temperature. Read only to derive `sh`: `sh = t_suc − Tsat(p_suc)`. |
| `t_liq` | °C | Liquid line temperature. Read only to derive `sc`: `sc = Tsat(p_dis) − t_liq`. |
| `i_fan` | A | Condenser fan current. Fan-fault discrimination becomes weaker. |
| `vib` | mm/s | Compressor vibration. Compressor-wear discrimination becomes weaker. |
| `t_dis` | °C | Discharge line temperature. |

Boolean columns may be `0/1`, `true/false`, `evet/hayır` (yes/no), `açık/kapalı` (on/off) or `on/off`.

## 2. Format rules

- **Separator**: `;`, `,` or tab is detected automatically.
- **Decimal**: both `12,5` and `12.5` are read (for Turkish Excel exports).
- **Encoding**: UTF-8 (with or without BOM) or Windows-1254 (Turkish) is detected automatically.
- **Timestamp**: the formats `05.01.2026 14:35`, `05.01.2026 14:35:20`, `2026-01-05 14:35:00` and
  `05/01/2026 14:35`. Day-first order (dd.mm.yyyy) is assumed. Times without a time zone are treated as UTC;
  times with an explicit offset are converted to UTC. If you send local time, include the correct offset.
- **Empty value**: an empty cell, `-`, `NaN` or `N/A` counts as missing. Unreadable values such as `Err` also count as
  missing and produce a warning.
- There must be no preamble/comment rows; the header is the first line of the file.

## 3. How should pressure be measured?

The model expects **absolute pressure (bar)**. Gauges and most controllers show **gauge (relative)** values;
absolute = gauge + 1.013 bar. If your data is gauge pressure, pass `--gauge`
(`load_csv(..., gauge=True)`) and the conversion is done automatically. If the unit is not bar (psi, kPa, MPa), convert
the file to bar before using it. A wrong unit is caught as `p_suc ≥ p_dis` or as physically impossible values and
causes an error.

Read the pressure sensors on the compressor's suction and discharge lines, close to the compressor; using the same point
as the value shown on the controller display keeps the data consistent.

## 4. Minimum data requirements

| Topic | Requirement |
|---|---|
| Duration | **At least 24 hours** of valid data per unit (otherwise an error). **3+ days** for reliable results, preferably several weeks. The first 12 hours are excluded from prediction while the feature window fills up. |
| Sampling | **1–5 minutes** is recommended. Data is aligned to a 5-minute grid (denser data is averaged). Data sparser than 5 minutes produces a warning; **data sparser than 30 minutes is rejected**. |
| Gaps | Gaps of up to 30 minutes are filled with the last value. Longer gaps stay missing; hours with less than half of their data present are left out of the report. |
| Duplicate rows | For the same unit and timestamp, the last row is kept. |
| Order | Rows do not need to be sorted. |
| Physical limits | Temperatures −60…80 °C (discharge line −60…150), pressure 0.1…45 bar, current 0…500 A. When `load_dataframe` is called directly, values outside these limits count as missing if only a small share is affected; if more than 10% are outside, the file is rejected. The analysis pipelines temporarily keep these readings for the sensor-quality check; the quality layer flags them and removes them from the model input. |
| Compressor | The compressor must have run at least a few times during the recording; the model evaluates values while it is running. |

## 5. Missing sensors

If sensors other than the 5 required signals are missing, the program **still runs**, but the evidence from that signal
cannot be used:

- Missing features are filled with the mean of the model's healthy training data (inside `FaultPredictor`, at
  prediction time). So a missing sensor counts as evidence neither for a "fault" nor for "healthy"; it does not
  produce false alarms. This filling is required because anomaly detection (Isolation Forest) does not accept NaN;
  for the classifier and the time-to-failure estimator, predictable behavior was likewise chosen over an ambiguous
  decision branch.
- Superheat and subcooling can physically be computed only with an extra temperature sensor (`t_suc`, `t_liq`);
  they are **not fabricated** from other signals.
- In a simulation trial (with no vibration, fan current, `sh`, `sc`, discharge temperature or coil temperature),
  healthy units raised no alarms and faults were still caught; however, **the fault type may be misattributed**
  (e.g. compressor wear may look like "condenser fouling"). To rely on the type prediction, add the related sensors:
  vibration → compressor, fan current → fan, `sh`/`sc` → refrigerant leak.

### 5.1 Sensor health (data quality) checks

Before reaching the model, uploaded data is also scanned for **sensor faults** (`sogutma/veri_kalitesi.py`); the goal is
to keep a stuck or drifting sensor from triggering an equipment-fault alarm. The checks run per unit on the 5-minute
data, hour by hour:

| Code | What it catches | How |
|---|---|---|
| `takili` (stuck) | Reading does not change | (max − min) is very small over 2 hours (12 running samples for compressor sensors) |
| `kopuk` (disconnected) | Constant, meaningless value | Out of range (e.g. −50 °C, 0 bar), or a constant 0 A / 0 mm/s reading while the compressor is running |
| `veri_kaybi` (data loss) | Missing data | More than half of the hour is empty (for sh/sc/current/vibration, data is expected only while the compressor is running) |
| `tutarsiz` (inconsistent) | Suspected drift / miscalibration | A physical relationship between sensors is broken: coil temperature ↔ suction saturation temperature ↔ room (during a long stop); discharge saturation temperature ↔ ambient ↔ discharge line (during a long stop); current/vibration ≈ 0 while stopped; while running, suction ≥ discharge pressure, room < evaporating temperature, or condensing < ambient |
| `gurultu` (noise) | Excessive noise period | Hourly median deviation is several times the sensor's normal noise |
| `ani_sicrama` (spike) | Single-sample spikes | Large deviation from the local (5-sample) median; spiking samples are **removed** |
| `aralik_disi` (out of range) | Isolated physically impossible values | Samples outside a plausible operating range are **removed** |

For sensors flagged as stuck, disconnected, data loss, inconsistent or noise, the related features (e.g. `p_suc` and
`evap_delta` for suction pressure) are removed from the model over the last 12 hours and treated as neutral (see
section 5: missing sensors). As a result:

- The `predict.py` unit summary shows a line such as `Veri kalitesi: Sensör şüphesi: Emme basıncı takılı (…)`
  ("Data quality: suspected sensor: suction pressure stuck"); the dashboard shows the same information as a warning
  and in a sensor × hour table. This is a diagnosis **separate** from the equipment-fault probability.
- Two columns are added to the hourly report: `sensor_sorunu` (sensor issue, True/False) and `sensor_notu`
  (sensor note, text).
- The check can be turned off with `python predict.py data.csv --sensor-kontrolu-kapat` (for comparison).

Limits: the thresholds were tuned by hand on synthetic data and must be recalibrated in the field. For signals with no
relationship to another sensor (superheat, subcooling, fan current, vibration), **drift cannot be caught**; drift may go
unnoticed in the first 1–3 days. The analysis pipeline hands out-of-range readings to the quality layer (e.g. 0 bar from
a disconnected pressure sensor) and cleans the flagged samples; when `load_dataframe` is called directly, the default
strict physical-limit validation still applies. From Python:
`sogutma.veri_kalitesi.degerlendir(raw)` → `KaliteRaporu` (`.saatlik`, `.temiz`, `.sorunlar()`, `.uygula(H)`).

## 6. Sample rows

Turkish Excel format (`;` separator, `,` decimal), `examples/ornek_veri.csv`:

```
Tarih Saat;Oda Sıcaklığı (°C);Dış Ortam Sıcaklığı (°C);Emme Basıncı (bar);Basma Basıncı (bar);Kompresör Akımı (A);...
01.01.2026 00:00;1,69;22,2;6,12;11,81;0,0;...
01.01.2026 00:05;1,88;22,2;6,13;11,75;0,0;...
```

A file with only the required columns (English headers, `,` separator, `.` decimal):

```
timestamp,t_amb,t_room,p_suc,p_dis,i_comp
2026-01-01 00:00:00,22.2,1.69,6.12,11.81,0.0
2026-01-01 00:05:00,22.2,1.88,6.13,11.75,0.0
2026-01-01 00:10:00,22.1,2.08,9.55,16.10,9.8
```

Multiple units: add a `unit_id` column to every row; each unit is analyzed separately.

## 7. Output

`predict.py` prints the latest state per unit (health score, status, predicted fault type, time to failure, the signals
that deviate most from normal) and produces an hourly report CSV:
`timestamp, unit_id, anomaly, p_<fault>…, health, status, pred_fault, confidence, eta_h, sensor_sorunu, sensor_notu`.

Usage from Python:

```python
from sogutma.ingest import load_csv
from sogutma.analiz import ham_tahmin
import joblib

raw = load_csv("data.csv", gauge=False, setpoint=2.0, tip="soguk_oda")
print(raw.attrs["rapor"].ozet())     # warnings, derived fields, missing sensors
model = joblib.load("models/predictor.joblib")
H, tahmin, kalite, H_temel = ham_tahmin(raw, model)
print(kalite.ozet())                  # sensor-issue summary (empty text if there is no issue)
```

`H` is the model input with the sensor-quality rules applied; `H_temel` holds the features before neutralization. Use
this shared pipeline in application code; calling `hourly_features(raw)` directly skips the sensor-quality check.
Importing `FaultPredictor` is not needed; use `joblib.load` only on the project's own trusted model file.

If the file cannot be used, a `ValidationError` is raised; `.hatalar` contains the (Turkish) error messages.

## 8. Exporting from a controller / data logger (general tips)

Devices and software vary widely; the points below are general tips, not a specific manufacturer's menu path.
For details, see your device's manual or ask your software vendor.

- Most controllers and monitoring software can export historical records as CSV/Excel; the software usually has a
  section called "history / trend / log / report".
- When exporting, choose the **shortest sampling interval** (1–5 min) and a date range of **several weeks**; if possible,
  put all units in the same file.
- Check that the low/high pressure, room and ambient temperature and compressor current channels are selected in the
  logging list. If compressor current is not available directly, it can be added with a current transformer /
  metering module.
- Check the pressure unit (bar/psi, gauge/absolute) in the export settings or the channel definition.
- Log instantaneous samples rather than averages only, if possible; overly thinned-out records (e.g. hourly) are
  rejected.
- An Excel "CSV (semicolon delimited)" file is read directly. If you edited the file in Excel, make sure the date
  column's format has not changed.
- Do not send records exported in local time without an offset: a time without a time zone is treated as UTC. Convert
  repeated/non-existent local times around daylight-saving transitions to ISO 8601 with an explicit offset; otherwise
  the correct instant cannot be determined reliably.
- Signals such as vibration and fan current are not available on most standard controllers; they may require a
  separate sensor/data logger. The system still works without these signals (see section 5).
