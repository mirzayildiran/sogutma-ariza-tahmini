from collections import Counter

import numpy as np
import pandas as pd
import pytest

from sogutma.faults import FAULT_TYPES
from sogutma.simulator import (
    COLUMNS,
    START,
    STEP_MIN,
    TIP_TURLERI,
    TIPLER,
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
    t = np.linspace(-45, 60, 22)
    assert np.allclose(sat_temperature(sat_pressure(t)), t)
    assert np.all(np.diff(sat_pressure(t)) > 0)


def test_saturation_plausible_for_r404a():
    # Dondurucu evaporasyonu (−30 °C) ≈ 2 bar; normal kaynama noktası (−46 °C) ≈ 1 bar
    assert 1.9 < float(sat_pressure(-30.0)) < 2.2
    assert 0.95 < float(sat_pressure(-46.2)) < 1.1
    # Soğuk oda aralığında eski üstel yaklaşıma yakın kalır
    t = np.linspace(-10, 45, 12)
    old = np.exp(1.766 + 0.02838 * t)
    assert np.all(np.abs(sat_pressure(t) / old - 1) < 0.06)


@pytest.mark.parametrize("tip", TIP_TURLERI)
def test_deterministic_for_same_seed(tip):
    u = Unit("D", "d", fault="gaz_kacagi", fault_start_h=10, fault_duration_h=40, tip=tip)
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
    # Tip karışımı: %50 soğuk oda, %25 dondurucu, %25 market dolabı; her tipte tüm arızalar mümkün
    assert Counter(u.tip for u in fleet) == {"soguk_oda": 30, "dondurucu": 15, "market_dolabi": 15}
    for u in fleet:
        lo, hi = TIPLER[u.tip].setpoint_aralik
        assert lo <= u.setpoint <= hi
    assert all(u.fault in FAULT_TYPES for u in fleet)
    faulty = [u for u in fleet if u.fault != "normal"]
    assert 0.55 < len(faulty) / 60 < 0.95  # fault_ratio=0.75
    assert len({u.fault for u in faulty}) >= 4
    for u in faulty:
        assert u.fault_start_h >= 3 * 24
        assert 4 * 24 <= u.fault_duration_h <= 14 * 24
        assert u.fault_start_h < days * 24  # arıza pencere içinde başlar
    # aynı tohum aynı filo
    assert [u.__dict__ for u in fleet] == [u.__dict__ for u in random_fleet(60, days, seed=4)]
    assert all(u.fault == "normal" for u in random_fleet(10, days, seed=1, fault_ratio=0))
    assert all(u.fault != "normal" for u in random_fleet(10, days, seed=1, fault_ratio=1))


def test_random_fleet_all_cold_rooms_matches_old_stream():
    # Tip ataması ayrı bir üreteçten gelir: yalnızca soğuk odalı filo eski akışla aynıdır
    cold = random_fleet(20, 20, seed=4, tip_agirlik={"soguk_oda": 1})
    assert {u.tip for u in cold} == {"soguk_oda"}
    mixed = random_fleet(20, 20, seed=4)
    for a, b in zip(cold, mixed):
        assert (a.fault, a.fault_start_h, a.fault_duration_h, a.capacity) == (
            b.fault, b.fault_start_h, b.fault_duration_h, b.capacity)


def test_random_fleet_all_faults_in_every_type():
    fleet = random_fleet(240, 30, seed=8)
    for tip in TIP_TURLERI:
        assert {u.fault for u in fleet if u.tip == tip} == set(FAULT_TYPES)


def test_unit_tip_validation_and_default_setpoint():
    assert Unit("a", "a").tip == "soguk_oda" and Unit("a", "a").setpoint == 2.0
    assert Unit("a", "a", tip="dondurucu").setpoint == -20.0
    assert Unit("a", "a", tip="market_dolabi").setpoint == 4.0
    assert Unit("a", "a", setpoint=-18.0, tip="dondurucu").setpoint == -18.0
    with pytest.raises(ValueError):
        Unit("a", "a", tip="chiller")


@pytest.fixture(scope="module")
def healthy_by_tip():
    return {t: simulate_unit(Unit(t, t, tip=t), days=5, seed=21) for t in TIP_TURLERI}


@pytest.mark.parametrize("tip", TIP_TURLERI)
def test_each_type_holds_setpoint_and_is_plausible(healthy_by_tip, tip):
    df = healthy_by_tip[tip]
    P = TIPLER[tip]
    assert (df["tip"] == tip).all()
    settled = df.iloc[12 * 6:]
    assert abs(settled["t_room"].mean() - P.setpoint) < 1.0
    assert settled["t_room"].between(P.setpoint - 4, P.setpoint + 4).all()
    run = df[df["comp_on"] & ~df["defrost"]]
    assert (run["p_suc"] < run["p_dis"]).all()
    assert (run["sh"] >= 0).all() and (run["sc"] >= 0).all() and (run["i_comp"] > 0).all()
    assert df["comp_on"].any() and df["defrost"].any()
    # Sağlıklı ünitede kompresör çalışma oranı makul (ne hep açık ne hep kapalı)
    assert 0.1 < settled["comp_on"].mean() < 0.7


def test_freezer_pressures_and_ratio(healthy_by_tip):
    run = lambda df: df[df["comp_on"] & ~df["defrost"]].iloc[12 * 6:]  # noqa: E731
    fr, cold = run(healthy_by_tip["dondurucu"]), run(healthy_by_tip["soguk_oda"])
    # R404A, ≈ −29 °C evaporasyon: emme basıncı ≈ 2 bar (mutlak)
    assert 1.6 < fr["p_suc"].mean() < 2.6
    assert 4.0 < cold["p_suc"].mean() < 6.0
    ratio = lambda d: (d["p_dis"] / d["p_suc"]).mean()  # noqa: E731
    assert ratio(fr) > 1.8 * ratio(cold)
    assert fr["t_dis"].mean() > cold["t_dis"].mean() + 8
    assert fr["t_coil"].mean() < -20  # batarya sıcaklığı dondurucuda çok düşük


def test_freezer_defrost_schedule(healthy_by_tip):
    d = healthy_by_tip["dondurucu"]
    starts = d.loc[d["defrost"] & ~d["defrost"].shift(fill_value=False), "timestamp"]
    assert (starts.diff().dropna() == pd.Timedelta(hours=8)).all()
    assert d["defrost"].sum() * STEP_MIN == 30 * len(starts)  # her defrost 30 dk


def test_display_cabinet_day_night_load_and_cycles(healthy_by_tip):
    m, c = healthy_by_tip["market_dolabi"], healthy_by_tip["soguk_oda"]
    for df in (m, c):
        df["h"] = df["timestamp"].dt.hour
    m = m.iloc[12 * 12:]
    day, night = m[m["h"].between(9, 20)], m[m["h"].isin([0, 1, 2, 3, 4, 5])]
    assert day["comp_on"].mean() > night["comp_on"].mean() + 0.1  # gece perdesi yükü düşürür
    # Erişim yalnızca mağaza saatlerinde
    assert night["door_open"].sum() == 0 and day["door_open"].mean() > 0.15
    starts = lambda df: (df["comp_on"].astype(int).diff() == 1).sum() / (len(df) / 12)  # noqa: E731
    assert starts(m) > 1.4 * starts(c.iloc[12 * 12:])  # daha sık, kısa çevrim
    # Gündüz yük değişkenliği: saatlik çalışma oranı gece olduğundan daha çok dalgalanır
    hourly_duty = m.set_index("timestamp")["comp_on"].astype(float).resample("1h").mean()
    assert hourly_duty[hourly_duty.index.hour.isin(range(9, 21))].std() > 0.05


@pytest.mark.parametrize("tip", TIP_TURLERI)
@pytest.mark.parametrize("fault, col, higher", [
    ("gaz_kacagi", "sh", True),
    ("evaporator_buzlanma", "sh", False),
    ("kondenser_kirlenmesi", "p_dis", True),
    ("kompresor_asinmasi", "vib", True),
    ("fan_arizasi", "i_fan", True),
])
def test_fault_signature_in_every_type(tip, fault, col, higher):
    base = Unit("B", "b", tip=tip)
    bad = Unit("F", "f", tip=tip, fault=fault, fault_start_h=0, fault_duration_h=48)
    a, b = simulate_unit(base, 3, seed=9), simulate_unit(bad, 3, seed=9)

    def last_day_mean(df):
        d = df.iloc[-24 * 12:]
        return d.loc[d["comp_on"] & ~d["defrost"], col].mean()

    assert (last_day_mean(b) > last_day_mean(a)) == higher


def test_demo_fleet():
    fleet = demo_fleet()
    assert len(fleet) == 12
    assert len({u.unit_id for u in fleet}) == 12
    assert {u.fault for u in fleet} == set(FAULT_TYPES)  # her arıza türü + sağlıklı
    assert Counter(u.tip for u in fleet) == {"soguk_oda": 8, "dondurucu": 2, "market_dolabi": 2}
    for tip in ("dondurucu", "market_dolabi"):  # her yeni tipte bir sağlıklı, bir arızalı
        assert sorted(u.fault == "normal" for u in fleet if u.tip == tip) == [False, True]
    # İlk 8 soğuk oda değişmedi
    assert [u.unit_id for u in fleet[:8]] == ["A1", "A2", "B1", "B2", "C1", "C2", "D1", "D2"]
    assert all(u.tip == "soguk_oda" for u in fleet[:8])
    for u in fleet:
        lo, hi = TIPLER[u.tip].setpoint_aralik
        assert u.name and lo <= u.setpoint <= hi
        if u.fault == "normal":
            assert np.isnan(u.failure_h)
        else:
            assert u.failure_h > u.fault_start_h
