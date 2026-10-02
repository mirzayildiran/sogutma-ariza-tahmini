import numpy as np
import pandas as pd
import pytest

from sogutma.features import FEATURE_LABELS, FEATURES, RUNNING_ONLY, WINDOW_H, hourly_features
from sogutma.simulator import START, Unit, simulate_fleet

DAYS = 5


@pytest.fixture(scope="module")
def raw():
    units = [
        Unit("N1", "n", setpoint=3.0),
        Unit("F1", "f", setpoint=2.0, fault="gaz_kacagi", fault_start_h=24, fault_duration_h=72),
    ]
    return simulate_fleet(units, DAYS, seed=2)


@pytest.fixture(scope="module")
def hourly(raw):
    return hourly_features(raw)


def test_feature_definitions():
    assert FEATURES == list(FEATURE_LABELS)
    assert set(RUNNING_ONLY) <= set(FEATURES)
    assert len(FEATURES) == len(set(FEATURES))


def test_expected_columns(hourly):
    for c in FEATURES + ["unit_id", "timestamp", "severity", "hours_to_failure", "fault", "label"]:
        assert c in hourly.columns, c


def test_no_nans_in_features(hourly):
    assert not hourly[FEATURES].isna().any().any()
    assert np.isfinite(hourly[FEATURES].to_numpy()).all()


def test_warmup_hours_dropped(hourly):
    for uid, g in hourly.groupby("unit_id"):
        assert g["timestamp"].min() == START + pd.Timedelta(hours=WINDOW_H)
        assert len(g) == DAYS * 24 - WINDOW_H
        assert g["timestamp"].is_monotonic_increasing


def test_value_ranges(hourly):
    assert hourly["duty"].between(0, 1).all()
    assert (hourly["starts"] >= 0).all()
    assert (hourly["p_suc"] > 0).all() and (hourly["p_dis"] > hourly["p_suc"]).all()


def test_labels_follow_severity_threshold(raw):
    for thr in (0.1, 0.5):
        H = hourly_features(raw, label_threshold=thr)
        n = H[H["unit_id"] == "N1"]
        assert (n["label"] == "normal").all()
        f = H[H["unit_id"] == "F1"]
        above = f["severity"] >= thr
        assert above.any() and (~above).any()
        assert (f.loc[above, "label"] == "gaz_kacagi").all()
        assert (f.loc[~above, "label"] == "normal").all()
    # eşik yükseldikçe arıza etiketli saat sayısı azalır
    low = (hourly_features(raw, 0.1)["label"] != "normal").sum()
    high = (hourly_features(raw, 0.5)["label"] != "normal").sum()
    assert high < low


def test_fault_shows_up_in_features(hourly):
    f = hourly[hourly["unit_id"] == "F1"]
    early = f[f["severity"] == 0].tail(12)
    late = f[f["severity"] >= 0.9].tail(12)
    assert late["sh"].mean() > early["sh"].mean() + 3  # gaz kaçağında kızgınlık artar
