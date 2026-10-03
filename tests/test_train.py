import inspect
import json

import pandas as pd

import train
from sogutma.faults import FAULT_TYPES
from sogutma.simulator import SENSOR_ARIZA_TURLERI, SENSORLER, START, TIP_TURLERI, Unit, random_fleet

from .conftest import DAYS, N_ROBUST, N_TEST, N_TRAIN


def test_defaults_unchanged():
    p = inspect.signature(train.main).parameters
    assert (p["n_train"].default, p["n_test"].default, p["days"].default) == (120, 60, 30)
    assert p["out_root"].default == train.ROOT


def test_outputs_written_to_out_root(trained_root):
    for rel in ("data/demo_raw.parquet", "data/demo_hourly.parquet", "data/demo_units.json",
                "models/predictor.joblib", "models/metrics.json"):
        assert (trained_root / rel).exists(), rel
    # repo klasörüne yazılmamalı
    assert trained_root != train.ROOT


def test_metrics_content(trained_root):
    m = json.loads((trained_root / "models/metrics.json").read_text())
    assert (m["n_train_units"], m["n_test_units"], m["days"]) == (N_TRAIN, N_TEST, DAYS)
    assert 0.8 < m["accuracy"] <= 1
    assert m["labels"] == FAULT_TYPES
    assert len(m["confusion_matrix"]) == len(FAULT_TYPES)
    assert len(m["early_warning"]) == N_TEST
    assert set(m["per_class"]) == set(FAULT_TYPES)
    p = m["probability_scores"]
    assert p["n_hours"] > 0 and 0 <= p["multiclass_brier"] <= 2 and p["log_loss"] >= 0
    assert p["calibrated"] is False
    assert p["n_units"] == N_TEST
    assert 0 <= p["reliability"]["macro_ece"] <= 1
    assert p["reliability"]["n_units"] == N_TEST
    assert set(p["unit_bootstrap_95_ci"]) == {"multiclass_brier", "log_loss"}
    assert all(len(ci) == 2 and ci[0] <= ci[1]
               for ci in p["unit_bootstrap_95_ci"].values())
    assert all("n_hours" in row and "n_units" in row
               for group in p["reliability"]["classes"].values() for row in group["bins"])
    assert all(sum(row["n_hours"] for row in group["bins"]) == p["n_hours"]
               for group in p["reliability"]["classes"].values())
    eta = m["eta_error"]
    assert eta["n_hours"] > 0 and eta["n_units"] > 0
    assert len(eta["mae_unit_bootstrap_95_ci_h"]) == 2
    assert 0 <= eta["median_abs_error_h"] <= eta["p90_abs_error_h"]
    assert eta["mae_h"] >= 0
    exact = eta["dogru_ariza_turu"]
    assert 0 < exact["n_hours"] <= eta["n_hours"]
    assert len(exact["mae_unit_bootstrap_95_ci_h"]) == 2
    assert 0 <= exact["median_abs_error_h"] <= exact["p90_abs_error_h"]
    seasonal = m["seasonal_shift"]
    assert set(seasonal) == {"-8C", "+8C"}
    assert all(v["ambient_offset_c"] in (-8.0, 8.0) and v["n_units"] == N_TEST
               and v["n_hours"] > 0 and v["n_failing_units"] > 0
               and 0 <= v["accuracy"] <= 1 for v in seasonal.values())
    # Tip bazında metrikler: her tip için ünite sayıları toplamı test filosuna eşit
    assert set(m["per_tip"]) == set(TIP_TURLERI)
    assert sum(v["n_units"] for v in m["per_tip"].values()) == N_TEST
    assert all(r["tip"] in TIP_TURLERI for r in m["early_warning"])
    for v in m["per_tip"].values():
        assert 0.8 < v["accuracy"] <= 1
        assert v["false_alarm_units"] <= v["n_units"]
        assert v["detection_rate"] is None or 0 <= v["detection_rate"] <= 1


def test_demo_data_consistent(trained_root):
    hourly = pd.read_parquet(trained_root / "data/demo_hourly.parquet")
    units = json.loads((trained_root / "data/demo_units.json").read_text())
    assert hourly["unit_id"].nunique() == len(units) == 12
    for c in ("health", "status", "pred_fault", "eta_h", "confidence"):
        assert c in hourly.columns
    assert units[0]["failure_h"] is not None
    assert {u["tip"] for u in units} == set(TIP_TURLERI)
    assert set(hourly["tip"]) == set(TIP_TURLERI)
    # Demo filosunda sensör arızası yoktur: katman hiçbir ünitede sensör şüphesi üretmez
    assert not hourly["sensor_sorunu"].any() and (hourly["sensor_notu"] == "").all()
    assert all(u["sensor_faults"] == [] for u in units)


def test_defaults_include_robustness_fleet():
    assert inspect.signature(train.main).parameters["n_robust"].default == 240


def test_robustness_metrics(trained_root):
    m = json.loads((trained_root / "models/metrics.json").read_text())
    r = m["robustness"]
    assert r["n_units"] == N_ROBUST and len(r["units"]) == N_ROBUST
    assert r["n_sensor_fault_units"] > 0
    assert set(r["by_kind"]) == set(SENSOR_ARIZA_TURLERI)
    assert any(u["fault"] != "normal" and u["sensor"] for u in r["units"])
    for g in ("healthy_with_sensor_fault", "healthy_without_sensor_fault"):
        assert 0 <= r[g]["false_alarm_with"] <= r[g]["n"] and 0 <= r[g]["false_alarm_without"] <= r[g]["n"]
    f = r["faulty_with_sensor_fault"]
    assert f["detected_with"] <= f["n_failing"] and f["detected_without"] <= f["n_failing"]
    d = r["sensor_detection"]
    assert d["n"] == r["n_sensor_fault_units"] and 0 <= d["detected"] <= d["n"]
    assert all(u["sensor"] and u["kind"] for u in r["units"] if u["sensor_detected"] is not None)


def test_robustness_fleet_assigns_every_combination_without_touching_equipment():
    units = train.robustness_fleet(240, 30, seed=3)
    base = random_fleet(240, 30, seed=3, fault_ratio=0.5)
    assert [{**u.__dict__, "sensor_faults": ()} for u in units] == [u.__dict__ for u in base]
    sf = [u for u in units if u.sensor_faults]
    healthy = {(u.sensor_faults[0].sensor, u.sensor_faults[0].kind) for u in sf if u.fault == "normal"}
    assert len(healthy) == len(SENSORLER) * len(SENSOR_ARIZA_TURLERI)
    assert all(u.sensor_faults[0].end_h() <= 30 * 24 + 1e-6 for u in sf)


def test_early_warning_detection_after_early_false_alarm_is_opt_in():
    u = Unit("U", "u", fault="gaz_kacagi", fault_start_h=100, fault_duration_h=100)
    ts = START + pd.to_timedelta(range(0, 200), unit="h")
    H = pd.DataFrame({"unit_id": "U", "timestamp": ts})
    # 20. saatten itibaren (arıza başlamadan) yanlış tür alarmı, 120'den sonra doğru tür
    p = pd.DataFrame({"status": ["Normal"] * 20 + ["İzlemede"] * 180,
                      "pred_fault": ["normal"] * 20 + ["fan_arizasi"] * 100 + ["gaz_kacagi"] * 80})
    varsayilan = train.early_warning(H, p, [u], days=30)[0]
    assert varsayilan["false_alarm"] and "detected" not in varsayilan
    ayrintili = train.early_warning(H, p, [u], days=30, tespit_yanlis_alarmdan_sonra=True)[0]
    assert ayrintili["false_alarm"] and ayrintili["detected"] and ayrintili["lead_h"] > 0


def test_seasonal_detection_rate_keeps_false_alarm_units_in_failure_denominator():
    warnings = [
        dict(fault="gaz_kacagi", fails_in_window=True, false_alarm=True, detected=False, lead_h=None),
        dict(fault="fan_arizasi", fails_in_window=True, false_alarm=False, detected=True, lead_h=24.0),
        dict(fault="normal", fails_in_window=False, false_alarm=True),
    ]
    m = train.seasonal_warning_summary(warnings)
    assert m["n_failing_units"] == 2
    assert m["detection_rate"] == 0.5
    assert m["false_alarm_units"] == 2
