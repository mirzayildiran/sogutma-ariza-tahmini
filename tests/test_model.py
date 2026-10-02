import numpy as np
import pandas as pd
import pytest

from sogutma.faults import FAULT_TYPES
from sogutma.features import FEATURE_LABELS, FEATURES, hourly_features
from sogutma.model import FaultPredictor, health_status
from sogutma.simulator import TIP_TURLERI, TIPLER, Unit, random_fleet, simulate_fleet

from .conftest import DAYS, N_TRAIN

FAULTS = FAULT_TYPES[1:]


@pytest.fixture(scope="module")
def eval_set(model):
    """Modelin görmediği tohumla: her ekipman tipinde 3 sağlıklı ünite + her arıza türünden bir ünite."""
    units = []
    for tip in TIP_TURLERI:
        sp = TIPLER[tip].setpoint
        units += [Unit(f"N{i}-{tip}", "n", setpoint=sp + (i - 1) * 0.5, ambient_offset=i - 1.5, tip=tip)
                  for i in range(3)]
        units += [Unit(f"F-{f}-{tip}", "f", setpoint=sp, fault=f, fault_start_h=3 * 24,
                       fault_duration_h=10 * 24, tip=tip) for f in FAULTS]
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
    assert set(m.baseline) <= set(TIP_TURLERI) and len(m.baseline) >= 1
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


@pytest.mark.parametrize("tip", TIP_TURLERI)
def test_healthy_units_are_mostly_normal(eval_set, tip):
    H, p = eval_set
    healthy = p[(H["fault"].eq("normal") & H["tip"].eq(tip)).to_numpy()]
    assert len(healthy) > 0
    assert (healthy["status"] == "Normal").mean() > 0.95
    assert healthy["health"].mean() > 80


@pytest.mark.parametrize("tip", TIP_TURLERI)
@pytest.mark.parametrize("fault", FAULTS)
def test_degraded_unit_is_detected_with_right_type(eval_set, fault, tip):
    H, p = eval_set
    mask = (H["unit_id"] == f"F-{fault}-{tip}").to_numpy()
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
    mask = (H["unit_id"] == "F-fan_arizasi-dondurucu").to_numpy()
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
    for tip in TIP_TURLERI:  # her tipte sağlıklı satır kendi tipinin referansına göre kıyaslanır
        mask = (H["unit_id"] == f"N1-{tip}").to_numpy()
        row = pd.concat([H[mask].iloc[100], p[mask].iloc[100]])
        assert len(model.explain(row)) <= 1


def test_features_are_relative_to_type_baseline(model, eval_set):
    """Dondurucunun normal emme basıncı (~2 bar) farklıdır; model girdisi tipe göre ortalanır."""
    H, _ = eval_set
    assert model.baseline["dondurucu"]["p_suc"] < 0.6 * model.baseline["soguk_oda"]["p_suc"]
    healthy = H[H["fault"].eq("normal")]
    dev = model._design(healthy)[:, : len(FEATURES) - 2]
    assert np.abs(dev.mean(axis=0)).max() < 2.0  # tipten bağımsız, sıfır civarı
    # explain, dondurucunun normal p_suc'unu anormal saymaz
    row = pd.concat([healthy[healthy["tip"] == "dondurucu"].iloc[100]])
    assert all(name != FEATURE_LABELS["p_suc"] for name, *_ in model.explain(row))


def test_trained_on_expected_size(model):
    assert N_TRAIN >= 20  # küçük filo yine de tüm sınıfları görür
    assert set(model.clf.classes_) == set(FAULT_TYPES)
