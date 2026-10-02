import numpy as np
import pandas as pd
import pytest

from sogutma.faults import FAULT_TYPES
from sogutma.simulator import (
    COLUMNS,
    START,
    STEP_MIN,
    Unit,
    demo_fleet,
    random_fleet,
    sat_pressure,
    sat_temperature,
    simulate_fleet,
    simulate_unit,
)


@pytest.fixture(scope="module")
def healthy():
    return simulate_unit(Unit("H1", "Sağlıklı", setpoint=3.0), days=4, seed=5)


def test_saturation_roundtrip():
    t = np.linspace(-30, 50, 9)
    assert np.allclose(sat_temperature(sat_pressure(t)), t)
    assert np.all(np.diff(sat_pressure(t)) > 0)


def test_deterministic_for_same_seed():
    u = Unit("D", "d", fault="gaz_kacagi", fault_start_h=10, fault_duration_h=40)
    a = simulate_unit(u, 3, seed=11)
    b = simulate_unit(u, 3, seed=11)
    pd.testing.assert_frame_equal(a, b)
    c = simulate_unit(u, 3, seed=12)
    assert not a["t_room"].equals(c["t_room"])


def test_shape_and_timestamps(healthy):
    assert len(healthy) == 4 * 24 * 60 // STEP_MIN
    assert set(COLUMNS) <= set(healthy.columns)
    assert healthy["timestamp"].iloc[0] == START
    assert (healthy["timestamp"].diff().dropna() == pd.Timedelta(minutes=STEP_MIN)).all()
    assert (healthy["unit_id"] == "H1").all()


def test_healthy_unit_is_physically_plausible(healthy):
    settled = healthy.iloc[12 * 6:]  # ilk 6 saat ısınma
    assert abs(settled["t_room"].mean() - 3.0) < 1.0
    assert settled["t_room"].between(3.0 - 3, 3.0 + 3).all()
    assert (healthy["p_suc"] > 0).all() and (healthy["p_dis"] > 0).all()
    run = healthy[healthy["comp_on"] & ~healthy["defrost"]]
    assert len(run) > 0
    assert (run["p_suc"] < run["p_dis"]).all()
    assert (run["sh"] >= 0).all() and (run["sc"] >= 0).all()
    assert (run["i_comp"] > 0).all()
    assert healthy["comp_on"].any() and healthy["defrost"].any() and healthy["door_open"].any()
    assert (healthy["severity"] == 0).all()
    assert healthy["hours_to_failure"].isna().all()
    assert healthy["fault"].eq("normal").all()


def test_compressor_off_signals(healthy):
    off = healthy[~healthy["comp_on"]]
    assert (off["i_comp"] == 0).all()
    assert off["sh"].isna().all() and off["sc"].isna().all()


def test_severity_curve():
    u = Unit("S", "s", fault="kompresor_asinmasi", fault_start_h=48, fault_duration_h=100)
    assert u.severity(0) == 0 and u.severity(47.9) == 0
    assert u.severity(48) == 0
    assert u.severity(98) == pytest.approx(0.5 ** 1.6)
    assert u.severity(148) == 1 and u.severity(500) == 1
    assert u.failure_h == 148
    assert Unit("N", "n").severity(100) == 0
    assert np.isnan(Unit("N", "n").failure_h)


def test_severity_in_simulation_is_zero_then_monotonic():
    u = Unit("S", "s", fault="gaz_kacagi", fault_start_h=24, fault_duration_h=48)
    df = simulate_unit(u, 5, seed=3)
    t_h = np.arange(len(df)) * STEP_MIN / 60
    sev = df["severity"].to_numpy()
    assert (sev[t_h < 24] == 0).all()
    assert (np.diff(sev) >= 0).all()
    assert sev.max() == 1.0
    assert (sev[t_h >= 72] == 1.0).all()
    assert df["fault"].eq("gaz_kacagi").all()


def test_hours_to_failure():
    u = Unit("S", "s", fault="fan_arizasi", fault_start_h=24, fault_duration_h=48)
    df = simulate_unit(u, 5, seed=3)
    t_h = np.arange(len(df)) * STEP_MIN / 60
    h = df["hours_to_failure"].to_numpy()
    assert h[0] == pytest.approx(72)  # arıza başlamadan da geri sayım var
    assert np.allclose(h[t_h <= 72], 72 - t_h[t_h <= 72])
    assert (h[t_h >= 72] == 0).all()
    assert (np.diff(h) <= 0).all()


@pytest.mark.parametrize("fault, col, higher", [
    ("gaz_kacagi", "sh", True),
    ("evaporator_buzlanma", "sh", False),
    ("kondenser_kirlenmesi", "p_dis", True),
    ("kompresor_asinmasi", "vib", True),
    ("fan_arizasi", "i_fan", True),
])
def test_fault_leaves_signature(fault, col, higher):
    base = Unit("B", "b", setpoint=2.0)
    bad = Unit("F", "f", setpoint=2.0, fault=fault, fault_start_h=0, fault_duration_h=48)
    a = simulate_unit(base, 3, seed=9)
    b = simulate_unit(bad, 3, seed=9)

    def last_day_mean(df):
        d = df.iloc[-24 * 12:]
        return d.loc[d["comp_on"] & ~d["defrost"], col].mean()

    assert (last_day_mean(b) > last_day_mean(a)) == higher


def test_simulate_fleet():
    units = [Unit("A", "a"), Unit("B", "b", fault="fan_arizasi", fault_start_h=5, fault_duration_h=30)]
    df = simulate_fleet(units, 2, seed=1)
    assert len(df) == 2 * 2 * 24 * 12
    assert df["unit_id"].unique().tolist() == ["A", "B"]
    pd.testing.assert_frame_equal(df, simulate_fleet(units, 2, seed=1))


def test_random_fleet():
    days = 20
    fleet = random_fleet(60, days, seed=4)
    assert len(fleet) == 60
    assert len({u.unit_id for u in fleet}) == 60
    assert all(u.fault in FAULT_TYPES for u in fleet)
    faulty = [u for u in fleet if u.fault != "normal"]
    assert 0.55 < len(faulty) / 60 < 0.95  # fault_ratio=0.75
    assert len({u.fault for u in faulty}) >= 4
    for u in faulty:
        assert u.fault_start_h >= 3 * 24
        assert 4 * 24 <= u.fault_duration_h <= 14 * 24
        assert u.fault_start_h < days * 24  # arıza pencere içinde başlar
    assert all(0 <= u.setpoint <= 5 for u in fleet)
    # aynı tohum aynı filo
    assert [u.__dict__ for u in fleet] == [u.__dict__ for u in random_fleet(60, days, seed=4)]
    assert all(u.fault == "normal" for u in random_fleet(10, days, seed=1, fault_ratio=0))
    assert all(u.fault != "normal" for u in random_fleet(10, days, seed=1, fault_ratio=1))


def test_demo_fleet():
    fleet = demo_fleet()
    assert len(fleet) == 8
    assert len({u.unit_id for u in fleet}) == 8
    assert {u.fault for u in fleet} == set(FAULT_TYPES)  # her arıza türü + sağlıklı
    for u in fleet:
        assert u.name and 0 <= u.setpoint <= 5
        if u.fault == "normal":
            assert np.isnan(u.failure_h)
        else:
            assert u.failure_h > u.fault_start_h
