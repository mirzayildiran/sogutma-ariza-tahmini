import numpy as np
import pandas as pd
import pytest

from sogutma.faults import FAULT_TYPES
from sogutma.features import FEATURE_LABELS, FEATURES, hourly_features
from sogutma.model import FaultPredictor, health_status
from sogutma.simulator import Unit, random_fleet, simulate_fleet

from .conftest import DAYS, N_TRAIN

FAULTS = FAULT_TYPES[1:]


@pytest.fixture(scope="module")
def eval_set(model):
    """Modelin görmediği tohumla: 4 sağlıklı ünite + her arıza türünden bir ünite."""
    units = [Unit(f"N{i}", "n", setpoint=1.0 + i, ambient_offset=i - 1.5) for i in range(4)]
    units += [Unit(f"F-{f}", "f", setpoint=2.0, fault=f, fault_start_h=3 * 24, fault_duration_h=10 * 24)
              for f in FAULTS]
    H = hourly_features(simulate_fleet(units, DAYS, seed=77))
    return H, model.predict(H)


def test_health_status_thresholds():
    assert health_status(100) == health_status(75) == "Normal"
    assert health_status(74.9) == health_status(50) == "İzlemede"
    assert health_status(49.9) == health_status(0) == "Kritik"


def test_fit_on_fleet_sets_up_components():
    units = random_fleet(6, 10, seed=3)
    H = hourly_features(simulate_fleet(units, 10, seed=3))
    m = FaultPredictor().fit(H)
    assert set(m.clf.classes_) <= set(FAULT_TYPES)
    assert list(m.normal_mean.index) == FEATURES
    assert m.score_p1 < m.score_p50


def test_predict_columns_and_ranges(model, eval_set):
    H, p = eval_set
    expected = ["anomaly", "health", "status", "pred_fault", "confidence", "eta_h"]
    expected += [f"p_{f}" for f in FAULT_TYPES]
    assert set(expected) <= set(p.columns)
    assert len(p) == len(H) and p.index.equals(H.index)
    assert p["health"].between(0, 100).all()
    assert p["anomaly"].between(0, 1).all()
    assert p["confidence"].between(0, 1).all()
    assert set(p["status"]) <= {"Normal", "İzlemede", "Kritik"}
    assert set(p["pred_fault"]) <= set(FAULT_TYPES)
    assert np.allclose(p[[f"p_{f}" for f in FAULT_TYPES]].sum(axis=1), 1, atol=1e-6)
    # Normal ünitede arıza tahmini yok, eta yalnızca arıza tahmininde dolu
    assert (p.loc[p["pred_fault"] == "normal", "eta_h"].isna()).all()
    assert (p.loc[p["pred_fault"] != "normal", "eta_h"] >= 0).all()
    assert ((p["status"] == "Normal") == (p["pred_fault"] == "normal")).all()


def test_healthy_units_are_mostly_normal(eval_set):
    H, p = eval_set
    healthy = p[H["fault"].eq("normal").to_numpy()]
    assert (healthy["status"] == "Normal").mean() > 0.95
    assert healthy["health"].mean() > 80


@pytest.mark.parametrize("fault", FAULTS)
def test_degraded_unit_is_detected_with_right_type(eval_set, fault):
    H, p = eval_set
    mask = (H["unit_id"] == f"F-{fault}").to_numpy()
    h, pp = H[mask], p[mask]
    end_of_life = ((h["hours_to_failure"] > 0) & (h["hours_to_failure"] <= 24)).to_numpy()
    assert end_of_life.sum() > 12
    last = pp[end_of_life]
    assert (last["status"] == "Kritik").mean() > 0.75
    assert (last["pred_fault"] == fault).mean() > 0.75
    # arıza başlamadan önce alarm yok, sağlık düşüyor
    before = pp[(h["severity"] == 0).to_numpy()]
    assert (before["status"] == "Normal").mean() > 0.9
    assert last["health"].mean() < before["health"].mean() - 30
    # arızaya yakınken tahmini kalan süre gerçeğe yakın mertebede (günler içinde)
    assert last["eta_h"].median() < 5 * 24


def test_explain_degraded_row(model, eval_set):
    H, p = eval_set
    mask = (H["unit_id"] == "F-fan_arizasi").to_numpy()
    h, pp = H[mask], p[mask]
    i = np.flatnonzero((h["hours_to_failure"] > 0).to_numpy())[-6]
    row = pd.concat([h.iloc[i], pp.iloc[i]])
    reasons = model.explain(row)
    assert 1 <= len(reasons) <= 4
    names = set(FEATURE_LABELS.values())
    zs = []
    for name, value, mean, z in reasons:
        assert name in names and name != FEATURE_LABELS["t_amb"]
        assert all(isinstance(v, float) and np.isfinite(v) for v in (value, mean, z))
        assert abs(z) >= 2
        zs.append(abs(z))
    assert zs == sorted(zs, reverse=True)
    assert any(name == FEATURE_LABELS["i_fan"] for name, *_ in reasons)  # fan arızasında fan akımı öne çıkar
    assert len(model.explain(row, top_n=2)) <= 2


def test_explain_healthy_row_has_few_reasons(model, eval_set):
    H, p = eval_set
    mask = (H["unit_id"] == "N1").to_numpy()
    row = pd.concat([H[mask].iloc[100], p[mask].iloc[100]])
    assert len(model.explain(row)) <= 1


def test_trained_on_expected_size(model):
    assert N_TRAIN >= 20  # küçük filo yine de tüm sınıfları görür
    assert set(model.clf.classes_) == set(FAULT_TYPES)
