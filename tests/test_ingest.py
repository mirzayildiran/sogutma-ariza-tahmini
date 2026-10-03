import io
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import predict
from sogutma.features import FEATURES, hourly_features
from sogutma.ingest import (
    MAX_GRID_POINTS,
    MAX_UNITS,
    OUT_COLUMNS,
    SCHEMA,
    ValidationError,
    load_csv,
    load_dataframe,
)
from sogutma.simulator import SENSORLER, SensorFault, Unit, simulate_unit
from sogutma.veri_kalitesi import OLASI_ARALIK, degerlendir

ORNEK = Path(__file__).parent.parent / "examples" / "ornek_veri.csv"


def _csv(df, sep=";", decimal=","):
    return io.StringIO(df.to_csv(sep=sep, decimal=decimal, index=False))


def _healthy(days=3, seed=5, **kw):
    """Sağlıklı simülatör verisi, Türkçe/İngilizce sütun adlarına çevrilmeye hazır."""
    return simulate_unit(Unit("A1", "a", setpoint=2.0, **kw), days, seed)


def _basic(raw):
    """Yalnızca zorunlu sütunlar (kısa İngilizce adlarla)."""
    out = raw[["timestamp", "t_amb", "t_room", "p_suc", "p_dis", "i_comp"]].copy()
    out["timestamp"] = out["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return out


def test_turkish_excel_example_parses():
    raw = load_csv(ORNEK)
    assert list(raw.columns) == OUT_COLUMNS
    assert len(raw) == 2880 and raw["unit_id"].nunique() == 1
    assert raw["timestamp"].is_monotonic_increasing
    assert raw["comp_on"].dtype == bool
    assert 0.5 < raw["t_room"].mean() < 4
    assert raw["p_suc"].between(1, 10).all()


def test_timezone_offsets_are_normalized_to_utc():
    raw = _healthy(2)[["timestamp", "t_amb", "t_room", "p_suc", "p_dis", "i_comp"]].copy()
    expected = raw["timestamp"].min() - pd.Timedelta(hours=3)
    raw["timestamp"] = raw["timestamp"].dt.tz_localize("Europe/Istanbul").map(lambda t: t.isoformat())
    got = load_csv(_csv(raw))
    assert got["timestamp"].min() == expected


def test_matches_simulator_values():
    sim = simulate_unit(Unit("A1", "a", setpoint=2.0), 3, seed=5)
    got = load_csv(_csv(_basic(sim)))
    assert len(got) == len(sim)
    assert np.allclose(got["t_room"], sim["t_room"], atol=1e-6)
    # comp_on sütunu yok: i_comp eşiğinden türetilir
    assert (got["comp_on"].to_numpy() == sim["comp_on"].to_numpy()).all()
    assert "comp_on" in got.attrs["rapor"].turetilen
    assert not got["defrost"].any()


def test_aliases_and_separators():
    sim = _healthy(2)
    df = pd.DataFrame(
        {
            "Zaman": sim["timestamp"].dt.strftime("%d.%m.%Y %H:%M"),
            "Dış Ortam (°C)": sim["t_amb"],
            "Oda Sıcaklığı [°C]": sim["t_room"],
            "Alçak Basınç": sim["p_suc"],
            "Yüksek Basınç": sim["p_dis"],
            "Kompresör Akımı": sim["i_comp"],
            "Ünite": "Oda-1",
        }
    )
    for sep, dec in [(";", ","), (",", "."), ("\t", ",")]:
        got = load_csv(_csv(df, sep, dec))
        assert (got["unit_id"] == "Oda-1").all()
        assert np.allclose(got["p_suc"], sim["p_suc"], atol=1e-6)


def test_cp1254_encoding_and_file_path(tmp_path):
    sim = _healthy(2)
    df = _basic(sim).rename(columns={"t_room": "Oda Sıcaklığı", "p_suc": "Emme Basıncı"})
    path = tmp_path / "veri.csv"
    path.write_bytes(df.to_csv(sep=";", decimal=",", index=False).encode("cp1254"))
    got = load_csv(path)
    assert len(got) == len(sim)


def test_separate_date_and_time_columns():
    sim = _healthy(2)
    df = _basic(sim).drop(columns="timestamp")
    df.insert(0, "Tarih", sim["timestamp"].dt.strftime("%d.%m.%Y"))
    df.insert(1, "Saat", sim["timestamp"].dt.strftime("%H:%M"))
    got = load_csv(_csv(df))
    assert got["timestamp"].iloc[0] == sim["timestamp"].iloc[0]


def test_gauge_conversion():
    sim = _healthy(2)
    df = _basic(sim)
    df["p_suc"] -= 1.013
    df["p_dis"] -= 1.013
    got = load_csv(_csv(df), gauge=True)
    assert np.allclose(got["p_suc"], sim["p_suc"], atol=1e-6)
    # Bayrak olmadan, mutlak olarak okunan düşük basınçlar için uyarı verilir
    df["p_suc"] -= 3
    assert any("gauge" in u for u in load_csv(_csv(df)).attrs["rapor"].uyarilar)


def test_duplicates_gaps_and_resampling():
    sim = _healthy(3)
    df = _basic(sim)
    stamp = df["timestamp"]
    df = pd.concat([df, df.iloc[100:110]])  # 10 yinelenen satır
    df = df[~df["timestamp"].isin(stamp.iloc[401:405]) & ~df["timestamp"].isin(stamp.iloc[1001:1100])]
    df = df.sample(frac=1, random_state=0)  # karışık sıra
    got = load_csv(_csv(df))
    rapor = got.attrs["rapor"]
    assert rapor.kopya_satir == 10
    assert len(got) == len(sim)  # 5 dk ızgarası korunur
    assert got["t_room"].iloc[401:405].notna().all()  # kısa boşluk doldurulur
    assert got["t_room"].iloc[1010:1099].isna().all()  # uzun boşluk NaN kalır
    H = hourly_features(got)
    gap_hours = got["timestamp"].iloc[1020:1080].dt.floor("1h").unique()
    assert not H["timestamp"].isin(gap_hours).any()  # boşluk saatleri raporlanmaz


def test_derives_sh_sc_from_auxiliary_temperatures():
    from sogutma.simulator import sat_temperature

    sim = _healthy(2)
    df = _basic(sim)
    df["t_suc"] = sat_temperature(sim["p_suc"]) + 7.0
    df["t_liq"] = sat_temperature(sim["p_dis"]) - 4.0
    got = load_csv(_csv(df))
    run = got["comp_on"]
    assert np.allclose(got.loc[run, "sh"], 7.0, atol=0.01)
    assert np.allclose(got.loc[run, "sc"], 4.0, atol=0.01)
    assert "sh" not in got.attrs["rapor"].eksik_sensorler


def test_missing_optional_sensors_reported():
    got = load_csv(_csv(_basic(_healthy(2))))
    assert set(got.attrs["rapor"].eksik_sensorler) == {"t_coil", "sh", "sc", "i_fan", "vib", "t_dis"}
    for c in ("vib", "i_fan", "sh"):
        assert got[c].isna().all()


def test_setpoint_param_and_column():
    df = _basic(_healthy(2))
    assert (load_csv(_csv(df), setpoint=3.5)["setpoint"] == 3.5).all()
    est = load_csv(_csv(df))
    assert est["setpoint"].nunique() == 1 and abs(est["setpoint"].iloc[0] - 2.0) < 1.5
    df["set"] = 2.5
    assert (load_csv(_csv(df), setpoint=9)["setpoint"] == 2.5).all()  # sütun parametreden önceliklidir


def test_multiple_units():
    a = _basic(_healthy(2, seed=1)).assign(unit_id="A")
    b = _basic(_healthy(2, seed=2)).assign(unit_id="B")
    got = load_csv(_csv(pd.concat([a, b])))
    assert set(got["unit_id"]) == {"A", "B"}
    assert set(hourly_features(got)["unit_id"]) == {"A", "B"}


# --- Doğrulama hataları ---


def _raises(df, *parts, **kw):
    with pytest.raises(ValidationError) as e:
        load_csv(_csv(df), **kw)
    for p in parts:
        assert p in str(e.value)


def test_missing_required_columns():
    _raises(_basic(_healthy(2)).drop(columns=["p_dis", "i_comp"]), "Zorunlu", "p_dis", "i_comp")


def test_unparseable_timestamps():
    df = _basic(_healthy(2))
    df["timestamp"] = "dün akşam"
    _raises(df, "Zaman damgası okunamadı")


def test_too_little_data():
    _raises(_basic(_healthy(2)).iloc[:200], "en az 24 saat")
    _raises(_basic(_healthy(2)).iloc[:1], "tek bir ölçüm")


def test_too_sparse_sampling():
    df = _basic(_healthy(10)).iloc[::9]  # 45 dk
    _raises(df, "örnekleme aralığı")


def test_analysis_limits_unit_count_and_aligned_grid_size():
    satir = dict(timestamp="2026-01-01 00:00:00", t_amb=24, t_room=2, p_suc=5, p_dis=15, i_comp=8)
    fazla_unite = pd.DataFrame([dict(satir, unit_id=f"U{i}") for i in range(MAX_UNITS + 1)])
    with pytest.raises(ValidationError, match=f"en fazla {MAX_UNITS} ünite"):
        load_dataframe(fazla_unite)

    rows = []
    for i in range(10):
        rows.extend([
            dict(satir, unit_id=f"U{i}", timestamp="2026-01-01 00:00:00"),
            dict(satir, unit_id=f"U{i}", timestamp="2026-12-27 00:05:00"),
        ])
    with pytest.raises(ValidationError, match=f"üst sınır {MAX_GRID_POINTS:,}"):
        load_dataframe(pd.DataFrame(rows))


def test_analysis_limit_rejects_excessive_time_span():
    rows = pd.DataFrame([
        dict(timestamp="2026-01-01 00:00:00", t_amb=24, t_room=2, p_suc=5, p_dis=15, i_comp=8),
        dict(timestamp="2027-01-03 00:05:00", t_amb=24, t_room=2, p_suc=5, p_dis=15, i_comp=8),
    ])
    with pytest.raises(ValidationError, match="analiz aralığı en fazla 366 gün"):
        load_dataframe(rows)


def test_impossible_temperatures():
    df = _basic(_healthy(2))
    df["t_room"] = df["t_room"] + 200
    _raises(df, "t_room", "fiziksel aralığın")


def test_sensor_fault_tolerant_ingest_retains_invalid_readings_for_quality_layer():
    df = _basic(_healthy(2, sensor_faults=(SensorFault("p_suc", "kopuk", 24),)))
    with pytest.raises(ValidationError, match="fiziksel aralığın"):
        load_dataframe(df)
    got = load_dataframe(df, allow_sensor_faults=True)
    assert (got["p_suc"] == 0).mean() > 0.1
    assert any("sensör sağlığı denetimine bırakıldı" in w for w in got.attrs["rapor"].uyarilar)


def test_quality_sensor_ranges_are_inside_ingest_schema_ranges():
    assert set(OLASI_ARALIK) == set(SENSORLER)
    for sensor, (lo, hi) in OLASI_ARALIK.items():
        schema_lo, schema_hi = SCHEMA[sensor]["aralik"]
        assert schema_lo <= lo <= hi <= schema_hi, sensor


def test_tolerant_ingest_still_routes_every_invalid_sensor_range_to_quality():
    df = _basic(_healthy(2))
    df["sc"] = 42.0  # ingest şeması üst sınırı 40 K, kalite sınırı da 40 K olmalı
    got = load_dataframe(df, allow_sensor_faults=True)
    quality = degerlendir(got)
    assert quality.saatlik["sc"].isin(["aralik_disi", "kopuk"]).any()

    df = _basic(_healthy(2))
    df["comp_on"] = True
    df["i_comp"] = -2.0  # ingest aralığı 0'dan başlar; kalite katmanı da bunu işaretlemeli
    got = load_dataframe(df, allow_sensor_faults=True)
    quality = degerlendir(got)
    assert quality.saatlik["i_comp"].isin(["aralik_disi", "kopuk"]).any()

    df["setpoint"] = 999.0  # kalite katmanının izlemediği metadata hâlâ katı doğrulanır
    with pytest.raises(ValidationError, match="setpoint.*fiziksel aralığın"):
        load_dataframe(df, allow_sensor_faults=True)


def test_few_outliers_become_nan_with_warning():
    df = _basic(_healthy(2))
    df.loc[df.index[10], "t_room"] = 999.0
    got = load_csv(_csv(df))
    assert any("t_room" in u for u in got.attrs["rapor"].uyarilar)


def test_swapped_pressures():
    df = _basic(_healthy(2)).rename(columns={"p_suc": "p_dis", "p_dis": "p_suc"})
    _raises(df, "emme basıncı basma basıncından büyük")


def test_empty_and_missing_file(tmp_path):
    with pytest.raises(ValidationError, match="boş"):
        load_csv(io.StringIO(""))
    with pytest.raises(ValidationError, match="bulunamadı"):
        load_csv(tmp_path / "yok.csv")


# --- Öznitelik / model entegrasyonu ---


def test_features_without_labels_match_simulator_values():
    sim = simulate_unit(
        Unit("A1", "a", setpoint=2.0, fault="gaz_kacagi", fault_start_h=24, fault_duration_h=48), 3, seed=9
    )
    labelled = hourly_features(sim)
    unlabelled = hourly_features(sim.drop(columns=["severity", "hours_to_failure", "fault"]))
    assert "label" not in unlabelled and "severity" not in unlabelled
    pd.testing.assert_frame_equal(labelled[unlabelled.columns], unlabelled)


def test_predict_tolerates_missing_sensors(model):
    sim = _healthy(4, seed=11)
    sim[["vib", "i_fan", "sh", "sc", "t_dis", "t_coil"]] = np.nan
    H = hourly_features(sim)
    assert H[["vib", "sh"]].isna().all().all()
    p = model.predict(H)
    assert not p.drop(columns="eta_h").isna().any().any()
    assert (p["status"] == "Normal").mean() > 0.9  # eksik sensör yanlış alarm üretmemeli


def test_predict_unchanged_without_nan(model):
    H = hourly_features(_healthy(4, seed=12))
    assert not H[FEATURES].isna().any().any()
    assert np.array_equal(model._impute(H), H[FEATURES].to_numpy())


def _freezer(days=3, seed=5, **kw):
    return simulate_unit(Unit("E1", "e", tip="dondurucu", **kw), days, seed)


def test_tip_default_param_and_column():
    got = load_csv(_csv(_basic(_healthy(2))))
    assert (got["tip"] == "soguk_oda").all() and "tip" in got.columns
    assert "tip" in got.attrs["rapor"].turetilen
    got = load_csv(_csv(_basic(_freezer(2))), tip="Dondurucu")
    assert (got["tip"] == "dondurucu").all()
    assert hourly_features(got)["tip_dondurucu"].eq(1).all()
    df = _basic(_freezer(2))
    df["Ekipman Tipi"] = "Dondurucu Oda"
    got = load_csv(_csv(df))
    assert (got["tip"] == "dondurucu").all() and "tip" not in got.attrs["rapor"].turetilen
    assert "tip" not in got.attrs["rapor"].eksik_sensorler


def test_tip_per_unit_and_errors():
    a, b = _basic(_healthy(2)), _basic(_freezer(2))
    a["unit_id"], b["unit_id"] = "A", "B"
    a["tip"], b["tip"] = "soguk_oda", "freezer"
    got = load_csv(_csv(pd.concat([a, b])))
    assert got.groupby("unit_id")["tip"].first().to_dict() == {"A": "soguk_oda", "B": "dondurucu"}
    with pytest.raises(ValidationError, match="ekipman tipi"):
        load_csv(_csv(_basic(_healthy(2))), tip="chiller")
    bad = _basic(_healthy(2))
    bad["tip"] = "chiller"
    with pytest.raises(ValidationError, match="tanınmayan"):
        load_csv(_csv(bad))


def test_freezer_data_without_tip_warns():
    got = load_csv(_csv(_basic(_freezer(2))))
    assert any("dondurucu" in u for u in got.attrs["rapor"].uyarilar)
    got = load_csv(_csv(_basic(_freezer(2))), tip="dondurucu")
    assert not any("tip" in u for u in got.attrs["rapor"].uyarilar)


def test_freezer_cli_with_tip(trained_root, tmp_path, capsys):
    csv = tmp_path / "dondurucu.csv"
    df = _freezer(4, fault="kompresor_asinmasi", fault_start_h=0, fault_duration_h=60)
    cols = ["timestamp", "t_amb", "t_room", "p_suc", "p_dis", "i_comp", "vib", "t_dis", "comp_on", "defrost"]
    out = df[cols]
    out.to_csv(csv, index=False)
    model = str(trained_root / "models" / "predictor.joblib")
    predict.main([str(csv), "--tip", "dondurucu", "-o", str(tmp_path / "r.csv"), "--model", model])
    text = capsys.readouterr().out
    assert "Sağlık skoru" in text and "tip parametresi" in text


# --- predict.py ---


def test_cli_end_to_end(trained_root, tmp_path, capsys):
    out = tmp_path / "rapor.csv"
    predict.main([str(ORNEK), "-o", str(out), "--model", str(trained_root / "models" / "predictor.joblib")])
    text = capsys.readouterr().out
    assert "Sağlık skoru" in text and "Saatlik rapor yazıldı" in text
    rep = pd.read_csv(out)
    assert {"timestamp", "unit_id", "health", "status", "pred_fault", "eta_h"} <= set(rep.columns)
    # Hata erken günlerde yok, sonda gelişiyor
    assert (rep["status"].iloc[:48] == "Normal").mean() > 0.9
    assert rep["status"].iloc[-1] != "Normal"


def test_cli_without_model_and_bad_data(tmp_path):
    with pytest.raises(SystemExit) as e:
        predict.main([str(ORNEK), "--model", str(tmp_path / "yok.joblib")])
    assert "train.py" in str(e.value)


def test_cli_bad_csv(trained_root, tmp_path):
    bad = tmp_path / "bozuk.csv"
    bad.write_text("a;b\n1;2\n", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        predict.main([str(bad), "--model", str(trained_root / "models" / "predictor.joblib")])
    assert "Zorunlu" in str(e.value)
