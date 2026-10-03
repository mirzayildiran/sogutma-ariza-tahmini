"""REST API testleri: model/veri geçici klasörden okunur (SOGUTMA_ROOT), küçük eğitilmiş model kullanılır."""

import asyncio
import logging
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from sogutma.api import VARSAYILAN_YUKLEME_MB, GovdeBoyutuSiniri, create_app, maks_yukleme_bayt
from sogutma.ingest import load_csv
from sogutma.simulator import SensorFault, Unit, simulate_unit

ORNEK = Path(__file__).parent.parent / "examples" / "ornek_veri.csv"


@pytest.fixture
def client(trained_root, monkeypatch):
    monkeypatch.setenv("SOGUTMA_ROOT", str(trained_root))
    monkeypatch.delenv("SOGUTMA_API_ANAHTARI", raising=False)
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def bos_client(tmp_path, monkeypatch):
    """Modelsiz klasör."""
    monkeypatch.setenv("SOGUTMA_ROOT", str(tmp_path))
    monkeypatch.delenv("SOGUTMA_API_ANAHTARI", raising=False)
    with TestClient(create_app()) as c:
        yield c


def csv_gonder(client, yol=ORNEK, **params):
    with open(yol, "rb") as f:
        return client.post("/tahmin/csv", files={"dosya": ("veri.csv", f, "text/csv")}, params=params)


def ornek_olcumler(gun=None):
    """Örnek CSV'yi API'nin JSON ölçüm biçimine çevirir (ağ geçidi gibi)."""
    raw = load_csv(ORNEK)
    if gun:
        raw = raw[raw["timestamp"] < raw["timestamp"].min() + pd.Timedelta(days=gun)]
    raw = raw.drop(columns=["tip"])
    kayitlar = raw.astype(object).where(raw.notna(), None).to_dict("records")
    for k in kayitlar:
        k["timestamp"] = pd.Timestamp(k["timestamp"]).isoformat()
        for c in ("comp_on", "defrost", "door_open"):
            k[c] = bool(k[c])
        for c, v in k.items():
            if isinstance(v, (np.floating, np.integer)):
                k[c] = float(v)
    return kayitlar


def test_saglik(client):
    r = client.get("/saglik")
    assert r.status_code == 200
    j = r.json()
    assert j["durum"] == "ok" and j["model_yuklu"] and j["model_surumu"] >= 1
    assert j["calisma_suresi_sn"] >= 0 and j["kimlik_dogrulama"] is False


def test_model_bilgisi(client):
    j = client.get("/model").json()
    assert j["sentetik"] is True and "sentetik" in j["uyari"].lower()
    assert any(a["kod"] == "gaz_kacagi" for a in j["ariza_turleri"])
    assert "dondurucu" in j["ekipman_tipleri"]
    m = j["metrikler"]
    assert 0 <= m["saatlik_dogruluk"] <= 1 and m["test_unite_sayisi"] > 0
    assert m["multiclass_brier"] >= 0 and m["log_loss"] >= 0 and m["eta_mae_saat"] >= 0
    assert m["eta_dogru_tur_mae_saat"] >= 0 and m["eta_dogru_tur_p90_mutlak_hata_saat"] >= 0
    assert set(m["ortam_kaymasi_stresi"]) == {"-8C", "+8C"}
    assert all(v["ortam_kaymasi_c"] in (-8, 8) for v in m["ortam_kaymasi_stresi"].values())
    assert set(m["tipe_gore"]) <= set(j["ekipman_tipleri"])


@pytest.mark.parametrize("value", ["nan", "inf", "0", "-1", "gecersiz"])
def test_gecersiz_istek_boyutu_ayari_varsayilana_doner(monkeypatch, value):
    monkeypatch.setenv("SOGUTMA_MAKS_YUKLEME_MB", value)
    assert maks_yukleme_bayt() == int(VARSAYILAN_YUKLEME_MB * 1024 * 1024)


def test_csv_tahmin_gaz_kacagi(client):
    r = csv_gonder(client)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["saatlik"] is None and "sentetik" in j["sentetik_uyari"]
    (u,) = j["uniteler"]
    assert u["unit_id"] == "U1"
    assert u["ariza"] == "gaz_kacagi" and u["ariza_adi"] == "Soğutucu gaz kaçağı"
    assert u["durum"] != "Normal" and u["uyari_saat"] > 0 and u["kalan_saat"] > 0
    assert 0 <= u["saglik"] <= 100 and 0 < u["guven"] <= 1
    assert abs(sum(u["olasiliklar"].values()) - 1) < 0.01
    assert u["sapmalar"] and {"sinyal", "deger", "normal", "sapma_sigma"} <= set(u["sapmalar"][0])
    assert u["oneri"]
    assert j["rapor"]["uniteler"]["U1"]["saat"] > 200
    assert "setpoint" not in j["rapor"]["turetilen"]  # örnek CSV'de set sütunu var


def test_csv_saatlik_seri(client):
    j = csv_gonder(client, saatlik="true", setpoint=1.0).json()
    assert len(j["saatlik"]) == j["uniteler"][0]["saat_sayisi"]
    assert {"zaman", "unit_id", "saglik", "durum", "ariza"} <= set(j["saatlik"][0])


def test_sensor_quality_is_exposed_for_csv_and_json(client, tmp_path):
    raw = simulate_unit(
        Unit("S1", "sensor testi", sensor_faults=(SensorFault("p_suc", "takili", 3 * 24),)),
        days=14,
        seed=14,
    )
    csv_path = tmp_path / "sensor.csv"
    raw.to_csv(csv_path, index=False)
    csv_result = csv_gonder(client, csv_path)
    assert csv_result.status_code == 200, csv_result.text
    csv_unit = csv_result.json()["uniteler"][0]
    assert csv_unit["sensor_sorunu"]
    assert "Emme basıncı" in csv_unit["sensor_notu"]
    assert any(x["sensor"] == "p_suc" and x["neden"] == "takili" for x in csv_unit["sensor_sorunlari"])

    records = raw.astype(object).where(pd.notna(raw), None).to_dict("records")
    for record in records:
        record["timestamp"] = pd.Timestamp(record["timestamp"]).isoformat()
        for field in ("comp_on", "defrost", "door_open"):
            record[field] = bool(record[field])
        for field, value in record.items():
            if isinstance(value, (np.floating, np.integer)):
                record[field] = float(value)
    json_result = client.post("/tahmin/olcumler", json={"olcumler": records, "saatlik": True})
    assert json_result.status_code == 200, json_result.text
    body = json_result.json()
    json_unit = body["uniteler"][0]
    assert json_unit["sensor_sorunu"] and json_unit["sensor_notu"] == csv_unit["sensor_notu"]
    assert json_unit["sensor_sorunlari"] == csv_unit["sensor_sorunlari"]
    assert any(row["sensor_sorunu"] for row in body["saatlik"])


def test_out_of_range_disconnected_pressure_reaches_sensor_quality_through_csv_and_json(client, tmp_path):
    raw = simulate_unit(
        Unit("S2", "kopuk basınç", sensor_faults=(SensorFault("p_suc", "kopuk", 3 * 24, 6 * 24),)),
        days=14,
        seed=15,
    )
    raw["timestamp"] = pd.to_datetime(raw["timestamp"]).dt.tz_localize("UTC").dt.tz_convert("Europe/Istanbul")
    csv_path = tmp_path / "kopuk.csv"
    raw.to_csv(csv_path, index=False)
    csv_result = csv_gonder(client, csv_path)
    assert csv_result.status_code == 200, csv_result.text
    csv_unit = csv_result.json()["uniteler"][0]
    assert any(x["sensor"] == "p_suc" and x["neden"] == "kopuk" for x in csv_unit["sensor_sorunlari"])

    records = raw.astype(object).where(pd.notna(raw), None).to_dict("records")
    for record in records:
        record["timestamp"] = pd.Timestamp(record["timestamp"]).isoformat()
        for field in ("comp_on", "defrost", "door_open"):
            record[field] = bool(record[field])
        for field, value in record.items():
            if isinstance(value, (np.floating, np.integer)):
                record[field] = float(value)
    json_result = client.post("/tahmin/olcumler", json={"olcumler": records})
    assert json_result.status_code == 200, json_result.text
    json_unit = json_result.json()["uniteler"][0]
    assert json_unit["sensor_sorunlari"] == csv_unit["sensor_sorunlari"]


def test_csv_gecersiz_tip_422(client):
    r = csv_gonder(client, tip="chiller")
    assert r.status_code == 422
    j = r.json()
    assert j["hata"] == "veri_gecersiz" and "ekipman tipi" in j["ayrintilar"][0]


def test_csv_bozuk_dosya_422(client, tmp_path):
    bozuk = tmp_path / "bozuk.csv"
    bozuk.write_text("a;b\n1;2\n", encoding="utf-8")
    r = csv_gonder(client, bozuk)
    assert r.status_code == 422 and "Zorunlu" in r.json()["ayrintilar"][0]


def test_csv_bos_dosya_422(client, tmp_path):
    bos = tmp_path / "bos.csv"
    bos.write_text("", encoding="utf-8")
    assert csv_gonder(client, bos).status_code == 422


def test_csv_kisa_dosya_422(client, tmp_path):
    satirlar = ORNEK.read_text(encoding="utf-8-sig").splitlines()[: 1 + 12 * 12]  # 12 saat
    kisa = tmp_path / "kisa.csv"
    kisa.write_text("\n".join(satirlar), encoding="utf-8")
    r = csv_gonder(client, kisa)
    assert r.status_code == 422 and "en az" in r.json()["mesaj"] + " ".join(r.json()["ayrintilar"])


def test_csv_dosya_alani_eksik_422(client):
    r = client.post("/tahmin/csv")
    assert r.status_code == 422
    assert r.json()["hata"] == "istek_gecersiz" and "dosya: zorunlu alan eksik" in r.json()["ayrintilar"]


def test_csv_yukleme_siniri(client, monkeypatch):
    monkeypatch.setenv("SOGUTMA_MAKS_YUKLEME_MB", "0.01")  # ~10 KB
    r = csv_gonder(client)
    assert r.status_code == 413 and r.json()["hata"] == "cok_buyuk"


def test_govde_siniri_content_length_olmayan_istegi_de_sinirlar(monkeypatch):
    monkeypatch.setenv("SOGUTMA_MAKS_YUKLEME_MB", "0.00001")
    parcalar = [b"012345678", b"abcdefgh"]
    yanitlar = []

    async def receive():
        if parcalar:
            return {"type": "http.request", "body": parcalar.pop(0), "more_body": bool(parcalar)}
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        yanitlar.append(message)

    async def downstream(scope, receive, send):
        while (await receive()).get("more_body"):
            pass
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    scope = {"type": "http", "method": "POST", "path": "/tahmin/olcumler", "headers": []}
    asyncio.run(GovdeBoyutuSiniri(downstream)(scope, receive, send))
    assert yanitlar[0]["status"] == 413
    assert b"cok_buyuk" in yanitlar[1]["body"]


def test_olcumler_tahmin(client):
    kayitlar = ornek_olcumler()
    r = client.post("/tahmin/olcumler", json={"olcumler": kayitlar, "saatlik": True})
    assert r.status_code == 200, r.text
    j = r.json()
    (u,) = j["uniteler"]
    assert u["ariza"] == "gaz_kacagi" and u["durum"] != "Normal"
    assert len(j["saatlik"]) == u["saat_sayisi"]
    csv_u = csv_gonder(client).json()["uniteler"][0]
    assert abs(u["saglik"] - csv_u["saglik"]) < 1.0  # aynı veri, aynı sonuç


def test_olcumler_birden_cok_unite_ve_tip(client):
    a = ornek_olcumler()
    b = [dict(k, unit_id="U2") for k in a]
    j = client.post("/tahmin/olcumler", json={"olcumler": a + b, "tip": "soguk_oda"}).json()
    assert [u["unit_id"] for u in j["uniteler"]] == ["U1", "U2"]
    assert j["rapor"]["turetilen"]["tip"] == "tip parametresi"


def test_olcumler_en_az_bir_gun(client):
    r = client.post("/tahmin/olcumler", json={"olcumler": ornek_olcumler(gun=0.4)})
    assert r.status_code == 422
    assert r.json()["hata"] == "veri_gecersiz" and "en az" in r.json()["ayrintilar"][0]


def test_olcumler_zaman_dilimli_timestamp_utcye_donusturulur(client):
    kayitlar = ornek_olcumler()
    for kayit in kayitlar:
        kayit["timestamp"] = pd.Timestamp(kayit["timestamp"]).tz_localize("Europe/Istanbul").isoformat()
    beklenen = (pd.Timestamp(kayitlar[-1]["timestamp"]).tz_convert("UTC").tz_localize(None)
                .floor("h").isoformat(timespec="seconds"))
    r = client.post("/tahmin/olcumler", json={"olcumler": kayitlar})
    assert r.status_code == 200, r.text
    assert r.json()["uniteler"][0]["zaman"] == beklenen


def test_olcumler_naif_ve_zaman_dilimli_timestamp_karistirilamaz(client):
    kayitlar = ornek_olcumler()
    kayitlar[-1]["timestamp"] += "+00:00"
    r = client.post("/tahmin/olcumler", json={"olcumler": kayitlar})
    assert r.status_code == 422
    assert "karıştırmayın" in " ".join(r.json()["ayrintilar"])


def test_olcumler_alan_hatalari_422(client):
    ok = ornek_olcumler(gun=0.1)[0]
    kotu = dict(ok, t_room="sıcak")
    eksik = {k: v for k, v in ok.items() if k != "p_suc"}
    r = client.post("/tahmin/olcumler", json={"olcumler": [kotu, eksik]})
    assert r.status_code == 422
    ayr = r.json()["ayrintilar"]
    assert "olcumler[0].t_room: sayı bekleniyor" in ayr
    assert "olcumler[1].p_suc: zorunlu alan eksik" in ayr


def test_olcumler_bos_liste_ve_bozuk_json_422(client):
    assert client.post("/tahmin/olcumler", json={"olcumler": []}).status_code == 422
    r = client.post("/tahmin/olcumler", content=b"{bozuk", headers={"content-type": "application/json"})
    assert r.status_code == 422 and "JSON okunamadı" in r.json()["ayrintilar"][0]


def test_model_yok_503(bos_client):
    yanitlar = (
        bos_client.get("/model"),
        csv_gonder(bos_client),
        bos_client.post("/tahmin/olcumler", json={"olcumler": ornek_olcumler(gun=0.1)}),
    )
    for r in yanitlar:
        assert r.status_code == 503
        assert r.json()["hata"] == "model_yok" and "train.py" in r.json()["mesaj"]
    s = bos_client.get("/saglik")
    assert s.status_code == 503 and s.json()["model_yuklu"] is False and s.json()["durum"] == "model_yok"


def test_eski_surum_model_503(trained_root, tmp_path, monkeypatch):
    model = joblib.load(trained_root / "models" / "predictor.joblib")
    model.version = 0
    (tmp_path / "models").mkdir()
    joblib.dump(model, tmp_path / "models" / "predictor.joblib")
    monkeypatch.setenv("SOGUTMA_ROOT", str(tmp_path))
    with TestClient(create_app()) as c:
        r = csv_gonder(c)
    assert r.status_code == 503 and r.json()["hata"] == "model_eski"


def test_model_sonradan_gelirse_yuklenir(trained_root, tmp_path, monkeypatch):
    """Model dosyası sonradan konursa (yeniden eğitim) servis yeniden başlatılmadan kullanır."""
    monkeypatch.setenv("SOGUTMA_ROOT", str(tmp_path))
    with TestClient(create_app()) as c:
        assert c.get("/saglik").status_code == 503
        (tmp_path / "models").mkdir()
        (tmp_path / "models" / "predictor.joblib").write_bytes(
            (trained_root / "models" / "predictor.joblib").read_bytes()
        )
        assert c.get("/saglik").status_code == 200


def test_api_anahtari(client, monkeypatch):
    monkeypatch.setenv("SOGUTMA_API_ANAHTARI", "gizli-deneme-anahtari")
    assert client.get("/saglik").status_code == 200  # sağlık denetimi açık kalır
    assert client.get("/saglik").json()["kimlik_dogrulama"] is True
    for r in (client.get("/model"), csv_gonder(client), client.post("/tahmin/olcumler", json={})):
        assert r.status_code == 401 and r.json()["hata"] == "yetkisiz"
    assert client.get("/model", headers={"X-API-Anahtari": "yanlis"}).status_code == 401
    dogru = {"X-API-Anahtari": "gizli-deneme-anahtari"}
    assert client.get("/model", headers=dogru).status_code == 200
    with open(ORNEK, "rb") as f:
        r = client.post("/tahmin/csv", files={"dosya": f}, headers=dogru)
    assert r.status_code == 200


def test_anahtar_yoksa_uyari_loglanir(trained_root, monkeypatch, caplog):
    monkeypatch.setenv("SOGUTMA_ROOT", str(trained_root))
    monkeypatch.delenv("SOGUTMA_API_ANAHTARI", raising=False)
    with caplog.at_level(logging.WARNING, logger="sogutma.api"):
        with TestClient(create_app()):
            pass
    assert "SOGUTMA_API_ANAHTARI ayarlı değil" in caplog.text


def test_openapi_turkce(client):
    j = client.get("/openapi.json").json()
    assert j["info"]["title"] == "Soğutma Arıza Tahmini API"
    assert {"/saglik", "/model", "/tahmin/csv", "/tahmin/olcumler"} <= set(j["paths"])
    assert client.get("/docs").status_code == 200
