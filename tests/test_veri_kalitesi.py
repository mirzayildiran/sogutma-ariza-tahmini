"""Sensör arızası enjeksiyonu, sensör sağlığı kontrolleri, öznitelik nötrleme ve uçtan uca akış."""

import io

import numpy as np
import pandas as pd
import pytest

import predict
from sogutma.analiz import analiz_et, ham_tahmin, son_durum
from sogutma.faults import FAULT_TYPES
from sogutma.features import FEATURES, hourly_features
from sogutma.simulator import (
    SENSOR_ARIZA_TURLERI,
    START,
    TIP_TURLERI,
    TIPLER,
    SensorFault,
    Unit,
    random_fleet,
    simulate_fleet,
    simulate_unit,
)
from sogutma.veri_kalitesi import NOTRLEYEN, SENSOR_OZNITELIK, degerlendir

DAYS = 12


def _sim(sensor=None, kind=None, start_h=3 * 24, duration_h=6 * 24, tip="soguk_oda", seed=4, days=DAYS,
         **unit_kw):
    faults = (SensorFault(sensor, kind, start_h, duration_h),) if sensor else ()
    u = Unit("T1", "t", tip=tip, sensor_faults=faults, **unit_kw)
    return simulate_unit(u, days, seed)


def _kodlar(raw, sensor):
    return degerlendir(raw).saatlik.loc["T1", sensor]


def _pencere(raw, start_h=3 * 24, duration_h=6 * 24):
    t0 = START + pd.Timedelta(hours=start_h)
    return t0, t0 + pd.Timedelta(hours=duration_h)


# ------------------------------------------------------------------ Simülatör: enjeksiyon
def test_sensor_fault_validation():
    with pytest.raises(ValueError, match="Bilinmeyen sensör"):
        SensorFault("t_xyz", "takili", 1)
    with pytest.raises(ValueError, match="arıza türü"):
        SensorFault("p_suc", "bozuk", 1)
    with pytest.raises(ValueError):
        SensorFault("p_suc", "takili", -1)
    with pytest.raises(ValueError):
        SensorFault("p_suc", "takili", 1, duration_h=0)
    u = Unit("a", "a", sensor_faults=[SensorFault("p_suc", "takili", 1)])
    assert u.sensor_faults[0].sensor == "p_suc"


@pytest.mark.parametrize("kind", SENSOR_ARIZA_TURLERI)
def test_injection_changes_only_that_sensor_inside_window(kind):
    base, bad = _sim(), _sim("p_suc", kind)
    t0, t1 = _pencere(bad)
    assert not base["p_suc"].equals(bad["p_suc"])
    # Diğer tüm sütunlar (fizik, etiketler dahil) birebir aynı: yalnızca ölçüm bozulur
    other = [c for c in base.columns if c != "p_suc"]
    pd.testing.assert_frame_equal(base[other], bad[other])
    # Pencere dışı örnekler değişmez
    dis = (bad["timestamp"] < t0) | (bad["timestamp"] >= t1)
    pd.testing.assert_series_equal(base.loc[dis, "p_suc"], bad.loc[dis, "p_suc"])


def test_default_fleet_is_bit_identical_without_sensor_faults():
    a = random_fleet(6, 10, seed=3)
    b = random_fleet(6, 10, seed=3, sensor_fault_ratio=0.0)
    assert [u.__dict__ for u in a] == [u.__dict__ for u in b]
    c = random_fleet(6, 10, seed=3, sensor_fault_ratio=1.0)
    assert all(len(u.sensor_faults) == 1 for u in c)
    # Ekipman parametreleri sensör arızası eklenince de aynı kalır (ayrı rastgele akış)
    assert [{**u.__dict__, "sensor_faults": ()} for u in c] == [u.__dict__ for u in a]
    pd.testing.assert_frame_equal(simulate_fleet(a, 4, 1), simulate_fleet(b, 4, 1))


def test_injection_is_deterministic():
    pd.testing.assert_frame_equal(_sim("t_room", "ani_sicrama"), _sim("t_room", "ani_sicrama"))


def test_stuck_freezes_last_good_value():
    d = _sim("p_suc", "takili")
    t0, t1 = _pencere(d)
    w = d[(d["timestamp"] >= t0) & (d["timestamp"] < t1)]["p_suc"]
    assert w.nunique() == 1
    onceki = d[d["timestamp"] < t0]["p_suc"].iloc[-1]
    assert w.iloc[0] == onceki


def test_disconnected_reads_implausible_constant():
    for sensor, deger in (("t_room", -50.0), ("p_suc", 0.0), ("i_comp", 0.0)):
        d = _sim(sensor, "kopuk")
        t0, t1 = _pencere(d)
        w = d[(d["timestamp"] >= t0) & (d["timestamp"] < t1)][sensor]
        assert (w == deger).all()


def test_drift_grows_linearly_and_stops_at_end():
    base, d = _sim(), _sim("t_room", "kayma")
    t0, t1 = _pencere(d)
    fark = (d["t_room"] - base["t_room"])
    assert fark[d["timestamp"] < t0].abs().max() == 0
    gun = fark[d["timestamp"] == t0 + pd.Timedelta(days=2)].iloc[0]
    assert gun == pytest.approx(2 * 0.5)  # t_room varsayılan kayma: 0,5 K/gün
    assert fark[d["timestamp"] >= t1].abs().max() == 0


def test_dropout_makes_nan_gaps_of_varying_length():
    d = _sim("p_dis", "veri_kaybi")
    t0, t1 = _pencere(d)
    nan = d["p_dis"].isna()
    assert not nan[d["timestamp"] < t0].any() and not nan[d["timestamp"] >= t1].any()
    assert 0.05 < nan.mean() < 0.4
    # ardışık NaN boşlukları: birden çok ve farklı uzunlukta
    run = nan.ne(nan.shift()).cumsum()[nan]
    uzunluk = run.groupby(run).size()
    assert uzunluk.max() >= 6  # ≥ 30 dk


def test_spikes_are_sparse_and_large_noise_has_larger_variance():
    base = _sim()
    sp = _sim("t_coil", "ani_sicrama")
    fark = (sp["t_coil"] - base["t_coil"]).abs()
    assert 0 < (fark > 0).sum() < 0.1 * len(sp)
    assert fark[fark > 0].min() > 5
    gur = _sim("t_coil", "gurultu")
    t0, t1 = _pencere(gur)
    ic = (gur["timestamp"] >= t0) & (gur["timestamp"] < t1)
    assert (gur.loc[ic, "t_coil"] - base.loc[ic, "t_coil"]).std() > 1.0


def test_gated_sensors_stay_nan_when_compressor_off():
    d = _sim("sh", "takili")
    assert d.loc[~d["comp_on"], "sh"].isna().all()
    assert d.loc[d["comp_on"] & ~d["defrost"], "sh"].notna().any()


# ------------------------------------------------------------------ Kontroller
@pytest.mark.parametrize("tip", TIP_TURLERI)
def test_healthy_units_raise_no_flags(tip):
    sp = TIPLER[tip].setpoint
    us = [Unit(f"H{i}", "h", setpoint=sp + i * 0.4, ambient_offset=i - 1.0, door_traffic=0.7 + 0.4 * i,
               tip=tip) for i in range(3)]
    k = degerlendir(simulate_fleet(us, 14, seed=21))
    assert k.sorunlar().empty


@pytest.mark.parametrize("fault", FAULT_TYPES[1:])
def test_equipment_faults_are_not_reported_as_sensor_faults(fault):
    """Ekipman arızası (ileri evre dahil) sensör arızası sanılmamalı."""
    for tip in TIP_TURLERI:
        u = Unit("T1", "t", tip=tip, fault=fault, fault_start_h=48, fault_duration_h=8 * 24)
        d = simulate_unit(u, 12, 5)
        assert degerlendir(d).sorunlar().empty, (fault, tip)


@pytest.mark.parametrize("sensor", ["p_suc", "t_room", "t_amb", "i_comp", "vib", "sh", "t_dis"])
def test_stuck_sensor_is_flagged_with_short_delay(sensor):
    d = _sim(sensor, "takili")
    t0, t1 = _pencere(d)
    k = _kodlar(d, sensor)
    ic = k[(k.index >= t0) & (k.index < t1)]
    # kompresör sensörleri yalnızca çalışma saatlerinde değerlendirilir (duruş saatleri boş kalır)
    assert ic.isin(["takili", "kopuk"]).mean() > (0.5 if sensor in ("i_comp", "vib", "sh") else 0.8)
    ilk = ic[ic.isin(["takili", "kopuk"])].index[0]
    assert ilk - t0 <= pd.Timedelta(hours=6)
    assert not k[k.index < t0].isin(NOTRLEYEN).any()
    # takılı örnekler temiz veride silinir
    temiz = degerlendir(d).temiz
    ic_satir = (temiz["timestamp"] >= t0 + pd.Timedelta(hours=8)) & (temiz["timestamp"] < t1)
    if sensor in ("i_comp", "vib", "sh"):  # yalnızca çalışırken değerlendirilir
        ic_satir &= temiz["comp_on"] & ~temiz["defrost"]
    assert temiz.loc[ic_satir, sensor].isna().all()


@pytest.mark.parametrize("sensor", ["t_room", "p_suc", "p_dis", "t_amb"])
def test_out_of_range_constant_is_flagged_as_disconnected(sensor):
    d = _sim(sensor, "kopuk")
    t0, t1 = _pencere(d)
    k = _kodlar(d, sensor)
    ic = k[(k.index >= t0 + pd.Timedelta(hours=1)) & (k.index < t1)]
    assert (ic == "kopuk").all()
    temiz = degerlendir(d).temiz
    assert temiz.loc[temiz["timestamp"] >= t0, sensor].isna().all() or \
        temiz.loc[(temiz["timestamp"] >= t0) & (temiz["timestamp"] < t1), sensor].isna().all()


def test_zero_current_while_running_is_disconnected():
    d = _sim("i_comp", "kopuk")
    t0, t1 = _pencere(d)
    k = _kodlar(d, "i_comp")
    ic = k[(k.index >= t0 + pd.Timedelta(hours=3)) & (k.index < t1)]
    assert (ic == "kopuk").mean() > 0.5 and not (ic == "takili").any()


def test_dropout_flagged_as_data_loss_by_hour():
    d = _sim("p_dis", "veri_kaybi")
    t0, t1 = _pencere(d)
    k = _kodlar(d, "p_dis")
    kayip = d.set_index("timestamp")["p_dis"].isna().resample("1h").mean()
    assert ((kayip >= 0.5) == (k == "veri_kaybi").reindex(kayip.index)).mean() > 0.97
    assert (k == "veri_kaybi").sum() > 5


def test_dropout_in_compressor_sensor_counts_only_running_samples():
    """sh/sc dururken zaten NaN: bu eksiklik veri kaybı sayılmamalı."""
    d = _sim()
    assert d["sh"].isna().mean() > 0.3
    assert not (_kodlar(d, "sh") == "veri_kaybi").any()


def test_spikes_are_removed_and_reported():
    d = _sim("p_suc", "ani_sicrama")
    k = degerlendir(d)
    t0, t1 = _pencere(d)
    base = _sim()
    sivri = (d["p_suc"] - base["p_suc"]).abs() > 0.5
    kalan = k.temiz.loc[sivri, "p_suc"]
    assert kalan.isna().mean() > 0.6  # sıçrayan örneklerin çoğu silindi (geçiş anları hariç)
    assert not k.temiz.loc[~sivri & (d["timestamp"] < t0), "p_suc"].isna().any()
    kod = k.saatlik.loc["T1", "p_suc"]
    assert (kod == "ani_sicrama").sum() > 3
    assert not kod.isin(NOTRLEYEN).any()  # ani sıçrama özniteliği nötrlemez, yalnızca temizler


def test_noise_burst_is_flagged():
    d = _sim("t_amb", "gurultu")
    t0, t1 = _pencere(d)
    k = _kodlar(d, "t_amb")
    assert (k[(k.index >= t0) & (k.index < t1)] == "gurultu").mean() > 0.6


@pytest.mark.parametrize("sensor", ["p_suc", "t_coil", "t_room", "p_dis", "t_amb", "t_dis"])
def test_drift_is_caught_by_cross_sensor_consistency(sensor):
    d = _sim(sensor, "kayma", duration_h=8 * 24, days=14)
    t0, t1 = _pencere(d, duration_h=8 * 24)
    k = _kodlar(d, sensor)
    ic = k[(k.index >= t0) & (k.index < t1)]
    assert (ic == "tutarsiz").sum() > 24
    assert not k[k.index < t0].isin(NOTRLEYEN).any()
    # kayma erken evrede (ilk gün) henüz eşik altındadır
    assert (ic.iloc[:12] == "").all()


def test_drift_in_sensors_without_a_cross_reference_is_not_detected():
    """Dürüst sınır: sh/sc gibi başka sensörle ilişkisi olmayan sinyalin kayması yakalanmaz."""
    d = _sim("sh", "kayma", duration_h=8 * 24, days=14)
    assert not _kodlar(d, "sh").isin(NOTRLEYEN).any()


def test_suction_not_below_discharge_check():
    d = _sim()
    d["p_suc"], d["p_dis"] = d["p_dis"].copy(), d["p_suc"].copy()  # yer değiştirmiş basınç sütunları
    k = degerlendir(d).saatlik.loc["T1"]
    assert (k["p_suc"] == "tutarsiz").mean() > 0.6 and (k["p_dis"] == "tutarsiz").mean() > 0.6


def test_zero_reference_check_flags_offset_current_when_compressor_off():
    d = _sim()
    d["i_comp"] = d["i_comp"] + 1.5  # dururken 1,5 A okuyan akım sensörü (sıfır kayması)
    k = degerlendir(d).saatlik.loc["T1", "i_comp"]
    assert (k == "tutarsiz").mean() > 0.8


def test_missing_optional_sensor_is_not_a_fault():
    d = _sim().drop(columns=["vib"])
    d["vib"] = np.nan
    k = degerlendir(d)
    assert "vib" not in set(k.sorunlar()["sensor"])


def test_report_summary_text():
    d = _sim("p_suc", "takili")
    k = degerlendir(d)
    s = k.sorunlar()
    assert {"unit_id", "sensor", "neden", "ilk", "son", "saat"} <= set(s.columns)
    assert "Emme basıncı takılı" in k.ozet()
    assert degerlendir(_sim()).ozet() == ""


# ------------------------------------------------------------------ Nötrleme
def test_neutralization_sets_dependent_features_nan_only_for_bad_sensor():
    d = _sim("p_suc", "takili", start_h=4 * 24, duration_h=7 * 24)
    k = degerlendir(d)
    H = hourly_features(k.temiz)
    H2, notlar = k.uygula(H)
    gun = H2["timestamp"] - START
    sonda = (gun >= pd.Timedelta(days=5)) & (gun < pd.Timedelta(days=11))
    for f in SENSOR_OZNITELIK["p_suc"]:
        assert H2.loc[sonda, f].isna().all(), f
    sag = [f for f in FEATURES if f not in SENSOR_OZNITELIK["p_suc"]]
    assert H2.loc[sonda, sag].notna().all().all()
    # sensör sağlam olduğu dönemde öznitelikler aynen korunur ve not boştur
    once = H2["timestamp"] < START + pd.Timedelta(days=4)
    pd.testing.assert_frame_equal(H2[once], H[once])
    assert not notlar.loc[once, "sensor_sorunu"].any()
    assert notlar.loc[sonda, "sensor_sorunu"].all()
    assert (notlar.loc[sonda, "sensor_notu"].str.startswith("Sensör şüphesi: Emme basıncı takılı")).all()
    assert H is not H2  # giriş değiştirilmez
    assert H["p_suc"].notna().all()


def test_neutralization_lasts_one_feature_window_after_recovery():
    d = _sim("p_suc", "takili", start_h=3 * 24, duration_h=2 * 24)
    k = degerlendir(d)
    H2, notlar = k.uygula(hourly_features(k.temiz))
    bit = START + pd.Timedelta(days=5)
    kotu = notlar.set_index(H2["timestamp"])["sensor_sorunu"]
    assert kotu[bit + pd.Timedelta(hours=6)]
    assert not kotu[bit + pd.Timedelta(hours=16)]


def test_no_flags_leaves_features_untouched():
    d = _sim()
    k = degerlendir(d)
    H = hourly_features(d)
    H2, notlar = k.uygula(hourly_features(k.temiz))
    pd.testing.assert_frame_equal(H, H2)
    assert not notlar["sensor_sorunu"].any() and (notlar["sensor_notu"] == "").all()


def test_stuck_room_sensor_does_not_drop_hours():
    d = _sim("t_room", "kopuk")
    H = hourly_features(degerlendir(d).temiz)
    assert len(H) >= len(hourly_features(_sim())) - 2


# ------------------------------------------------------------------ Uçtan uca
def _fleet_for(sensor_faults, fault="normal", days=15):
    u = Unit("T1", "t", fault=fault, fault_start_h=3 * 24, fault_duration_h=10 * 24,
             sensor_faults=sensor_faults)
    return simulate_fleet([u], days, seed=8)


def _sustained(pred, n=6):
    return bool(((pred["status"] != "Normal").astype(int).rolling(n).sum() == n).any())


def test_end_to_end_healthy_unit_with_stuck_suction_sensor(model):
    raw = _fleet_for((SensorFault("p_suc", "takili", 5 * 24),))
    H, pred, kalite, _ = ham_tahmin(raw, model)
    assert not _sustained(pred), "sensör arızası ekipman arızası alarmı üretmemeli"
    assert (pred["pred_fault"] == "normal").all() or not _sustained(pred)
    # sensör sorunu raporlandı
    son = pred.iloc[-1]
    assert bool(son["sensor_sorunu"]) and "Emme basıncı takılı" in son["sensor_notu"]
    d = son_durum(pred, H, model)
    assert d["sensor_sorunu"] and "Sensör şüphesi" in d["sensor_notu"]
    # öznitelikler nötr: açıklama listesinde emme basıncı sapması çıkmaz
    assert all("Emme basıncı" not in ad for ad, *_ in d["sapmalar"])
    s = kalite.sorunlar()
    assert list(s["sensor"]) == ["p_suc"] and list(s["neden"]) == ["takili"]


def test_end_to_end_stuck_ambient_sensor_false_alarm_is_prevented(model):
    """Takılı dış sıcaklık sensörü katman olmadan sağlıklı üniteyi arızalı gösterir; katmanla göstermez."""
    raw = _fleet_for((SensorFault("t_amb", "takili", 5 * 24),))
    _, pred0, _, _ = ham_tahmin(raw, model, sensor_kontrolu=False)
    _, pred1, _, _ = ham_tahmin(raw, model)
    assert _sustained(pred0)
    assert not _sustained(pred1)
    assert "Dış ortam sıcaklığı takılı" in pred1.iloc[-1]["sensor_notu"]


def test_end_to_end_real_fault_still_detected_with_healthy_sensors(model):
    raw = _fleet_for((), fault="gaz_kacagi")
    H, pred, kalite, _ = ham_tahmin(raw, model)
    assert kalite.sorunlar().empty and not pred["sensor_sorunu"].any()
    assert _sustained(pred)
    assert pred.iloc[-1]["pred_fault"] == "gaz_kacagi"
    # katman kapalıyken de aynı tahmin (sağlam sensörlerde katman sonucu değiştirmez)
    _, pred0, _, _ = ham_tahmin(raw, model, sensor_kontrolu=False)
    pd.testing.assert_frame_equal(pred.drop(columns=["sensor_sorunu", "sensor_notu"]), pred0)


def test_end_to_end_real_fault_still_detected_despite_unrelated_sensor_fault(model):
    raw = _fleet_for((SensorFault("t_dis", "kopuk", 3 * 24),), fault="gaz_kacagi")
    _, pred, kalite, _ = ham_tahmin(raw, model)
    assert "Basma hattı sıcaklığı" in pred.iloc[-1]["sensor_notu"]
    assert _sustained(pred) and pred.iloc[-1]["pred_fault"] == "gaz_kacagi"


def _csv(raw):
    cols = ["timestamp", "t_amb", "t_room", "p_suc", "p_dis", "i_comp", "i_fan", "vib", "t_dis", "sh", "sc",
            "t_coil", "comp_on", "defrost", "setpoint"]
    buf = io.StringIO()
    df = raw[cols].assign(comp_on=raw["comp_on"].astype(int), defrost=raw["defrost"].astype(int))
    df.to_csv(buf, index=False)
    return io.BytesIO(buf.getvalue().encode())


def test_analiz_et_reports_sensor_issue_from_csv(model):
    raw = _fleet_for((SensorFault("p_suc", "takili", 5 * 24),))
    a = analiz_et(_csv(raw), model)
    assert a.kalite is not None and list(a.sensor_sorunlari()["sensor"]) == ["p_suc"]
    H, p = a.unite("U1")
    d = son_durum(p, H, model)
    assert d["sensor_sorunu"] and "Emme basıncı" in d["sensor_notu"]
    assert "sensor_sorunu" in a.rapor_tablosu().columns
    ozet = predict.summarize("U1", p, H, model, a.sensor_sorunlari("U1"))
    assert "Veri kalitesi: Sensör şüphesi: Emme basıncı takılı" in ozet
    # katman kapatılabilir
    kapali = analiz_et(_csv(raw), model, sensor_kontrolu=False)
    assert kapali.kalite is None and kapali.sensor_sorunlari().empty
    assert not son_durum(kapali.pred, kapali.H, model)["sensor_sorunu"]
