"""Bildirim modülü: olay üretimi, kurallar, kanallar (ağ taklit edilir), durum dosyası, predict.py."""

import hashlib
import hmac
import json
import smtplib
import urllib.error
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

import predict
from sogutma import bildirim
from sogutma.bildirim import (
    AyarHatasi,
    Gonderici,
    Kurallar,
    SessizSaatler,
    ayar_yukle,
    imza,
    isle,
    kisa_mesaj,
    olaylari_uret,
    uzun_mesaj,
)

ROOT = Path(__file__).resolve().parent.parent
ORNEK_AYAR = ROOT / "examples" / "bildirim_ayarlari.ornek.json"
ORNEK_CSV = ROOT / "examples" / "ornek_veri.csv"

N, I, K = "Normal", "İzlemede", "Kritik"  # noqa: E741


def frame(durumlar, ariza="gaz_kacagi", guven=0.9, baslangic="2026-03-01 00:00", uid="A1", eta=48.0):
    """Saatlik tahmin tablosu; durumlar: durum listesi. guven: sayı ya da liste."""
    n = len(durumlar)
    guven = guven if isinstance(guven, list) else [guven] * n
    return pd.DataFrame(
        {
            "timestamp": pd.date_range(baslangic, periods=n, freq="h"),
            "unit_id": uid,
            "health": [92.0 if d == N else 60.0 if d == I else 30.0 for d in durumlar],
            "status": durumlar,
            "pred_fault": [("normal" if d == N else ariza) for d in durumlar],
            "confidence": guven,
            "eta_h": [None if d == N else eta for d in durumlar],
        }
    )


def kural(**kw):
    return Kurallar.from_dict(kw)


def turler(olaylar):
    return [o.tur for o in olaylar]


# --------------------------------------------------------------------------- geçişler


def test_gecisler_uyari_yukselis_iyilesme():
    pred = frame([N] * 10 + [I] * 8 + [K] * 5 + [N] * 8)
    olaylar = olaylari_uret(pred, Kurallar(), bildirim.bos_durum())
    assert turler(olaylar) == ["uyari", "yukselis", "iyilesme"]
    assert [o.durum for o in olaylar] == [I, K, N]
    assert [o.onceki for o in olaylar] == [N, I, K]
    # İzlemede: 6. kalıcı saatte (indeks 15); Kritik: 3. Kritik saatte (indeks 20)
    assert pd.Timestamp(olaylar[0].zaman) == pred["timestamp"].iloc[15]
    assert pd.Timestamp(olaylar[1].zaman) == pred["timestamp"].iloc[20]
    assert olaylar[0].ariza == "gaz_kacagi"
    assert olaylar[2].ariza == "gaz_kacagi"  # iyileşen arıza adı korunur


def test_dogrudan_kritik():
    olaylar = olaylari_uret(frame([N] * 5 + [K] * 4), Kurallar(), bildirim.bos_durum())
    assert turler(olaylar) == ["kritik"]


def test_hep_normal_olay_uretmez():
    assert olaylari_uret(frame([N] * 48), Kurallar(), bildirim.bos_durum()) == []


def test_bildirilen_durumlar_ve_iyilesme_ayari():
    pred = frame([N] * 3 + [I] * 8 + [N] * 8)
    assert olaylari_uret(pred, kural(bildirilen_durumlar=["Kritik"]), bildirim.bos_durum()) == []
    o = olaylari_uret(pred, kural(iyilesme_bildir=False), bildirim.bos_durum())
    assert turler(o) == ["uyari"]


def test_ariza_turu_degisince_bildirilir():
    pred = frame([N] * 2 + [I] * 6)
    pred2 = frame([I] * 8, ariza="fan_arizasi", baslangic="2026-03-01 08:00")
    tum = pd.concat([pred, pred2], ignore_index=True)
    o = olaylari_uret(tum, kural(surekli_saat=3), bildirim.bos_durum())
    assert turler(o) == ["uyari", "ariza_degisti"]
    assert o[1].ariza == "fan_arizasi"


def test_hatirlatma_cooldown_sonrasi():
    pred = frame([I] * 40)
    assert turler(olaylari_uret(pred, kural(surekli_saat=3), bildirim.bos_durum())) == ["uyari"]
    o = olaylari_uret(pred, kural(surekli_saat=3, hatirlatma=True, cooldown_saat=12), bildirim.bos_durum())
    assert turler(o) == ["uyari"] + ["hatirlatma"] * 3
    gap = pd.Timestamp(o[1].zaman) - pd.Timestamp(o[0].zaman)
    assert gap >= pd.Timedelta(hours=12)


# --------------------------------------------------------------------------- cooldown / süreklilik / güven


def test_cooldown_ayni_ariza_tekrar_gondermez():
    pred = frame([I] * 3 + [N] * 2 + [I] * 3)
    k = kural(surekli_saat=3, iyilesme_saat=2, cooldown_saat=12)
    assert turler(olaylari_uret(pred, k, bildirim.bos_durum())) == ["uyari", "iyilesme"]
    k0 = kural(surekli_saat=3, iyilesme_saat=2, cooldown_saat=0)
    assert turler(olaylari_uret(pred, k0, bildirim.bos_durum())) == ["uyari", "iyilesme", "uyari"]


def test_cooldown_farkli_ariza_engellenmez():
    a = frame([I] * 3 + [N] * 2, ariza="gaz_kacagi")
    b = frame([I] * 3, ariza="fan_arizasi", baslangic="2026-03-01 05:00")
    o = olaylari_uret(pd.concat([a, b]), kural(surekli_saat=3, iyilesme_saat=2), bildirim.bos_durum())
    assert [x.ariza for x in o if x.tur == "uyari"] == ["gaz_kacagi", "fan_arizasi"]


def test_surekli_saat_kurali():
    pred = frame([N] * 3 + [I] * 5)
    assert olaylari_uret(pred, Kurallar(), bildirim.bos_durum()) == []  # varsayılan 6 saat
    assert turler(olaylari_uret(pred, kural(surekli_saat=5), bildirim.bos_durum())) == ["uyari"]
    # Kesintili (dalgalanan) durum sürekli sayılmaz
    dalga = frame([I, N] * 20)
    assert olaylari_uret(dalga, kural(surekli_saat=2, iyilesme_saat=3), bildirim.bos_durum()) == []


def test_min_guven():
    dusuk = frame([N] * 2 + [I] * 8, guven=0.4)
    assert olaylari_uret(dusuk, kural(min_guven=0.5), bildirim.bos_durum()) == []
    # Güven sonradan yükselince olay o satırda üretilir
    g = [0.9] * 2 + [0.4] * 6 + [0.8] * 4
    o = olaylari_uret(frame([N] * 2 + [I] * 10, guven=g), kural(min_guven=0.5), bildirim.bos_durum())
    assert turler(o) == ["uyari"]
    assert o[0].guven == pytest.approx(0.8)


def test_cok_eski_olay_gonderilmez_ama_durum_islenir():
    pred = frame([N] * 5 + [I] * 8 + [N] * 150)
    d = bildirim.bos_durum()
    assert olaylari_uret(pred, kural(en_eski_olay_saat=48), d) == []
    assert d["birimler"]["A1"]["seviye"] == 0
    assert len(olaylari_uret(pred, kural(en_eski_olay_saat=None), bildirim.bos_durum())) == 2


def test_suren_eski_alarm_bir_kez_bildirilir():
    # Geçiş 48 saatten eski ama alarm hâlâ sürüyor: güncel durum yine de bir kez bildirilir
    pred = frame([N] * 5 + [I] * 80)
    d = bildirim.bos_durum()
    o = olaylari_uret(pred, kural(en_eski_olay_saat=48), d)
    assert turler(o) == ["uyari"] and pd.Timestamp(o[0].zaman) == pred["timestamp"].iloc[-1]
    assert olaylari_uret(pred, kural(en_eski_olay_saat=48), d) == []  # ikinci çalıştırma: tekrar yok


def test_birim_adi():
    k = kural(birim_adlari={"A1": "Soğuk Oda A1"})
    o = olaylari_uret(frame([K] * 5), k, bildirim.bos_durum())[0]
    assert o.ad == "Soğuk Oda A1"
    assert olaylari_uret(frame([K] * 5, uid="B2"), k, bildirim.bos_durum())[0].ad == "Ünite B2"


# --------------------------------------------------------------------------- sessiz saat


def test_sessiz_saat_araligi():
    s = SessizSaatler(aktif=True, baslangic="22:00", bitis="07:00")
    assert s.icinde("2026-03-01 23:30") and s.icinde("2026-03-02 06:59")
    assert not s.icinde("2026-03-02 07:00") and not s.icinde("2026-03-02 12:00")
    gun = SessizSaatler(aktif=True, baslangic="09:00", bitis="17:00")
    assert gun.icinde("2026-03-02 09:00") and not gun.icinde("2026-03-02 17:00")
    assert not SessizSaatler(aktif=False).icinde("2026-03-02 23:00")


def _kayit_gonderici(k, kayit):
    return Gonderici(k, deneme=False, cikti=kayit.append, uyku=lambda s: None)


def test_sessiz_saatte_ertelenir_sonra_gonderilir(tmp_path):
    k = kural(sessiz_saatler={"aktif": True, "baslangic": "22:00", "bitis": "07:00"}, surekli_saat=3)
    durum = tmp_path / "durum.json"
    # 20:00-23:00 arası İzlemede: olay 22:00 sonrası üretilir, sessiz saatte beklemeli
    pred = frame([N] * 2 + [I] * 4, baslangic="2026-03-01 19:00")  # son satır 00:00
    kayit = []
    r = isle(pred, k, durum, _kayit_gonderici(k, kayit))
    assert kayit == [] and len(r.ertelenen) == 1
    assert json.loads(durum.read_text())["bekleyen"][0]["tur"] == "uyari"
    # Sabah: yeni veri, sessiz saat bitti; bekleyen olay çıkar
    sabah = frame([I] * 3, baslangic="2026-03-02 07:00")
    r2 = isle(pd.concat([pred, sabah]), k, durum, _kayit_gonderici(k, kayit))
    assert len(kayit) == 1 and "İZLEMEDE" in kayit[0] and r2.ertelenen == []
    assert json.loads(durum.read_text())["bekleyen"] == []


def test_sessiz_saatte_kritik_haric(tmp_path):
    pred = frame([N] * 2 + [K] * 4, baslangic="2026-03-01 21:00")
    ss = {"aktif": True, "baslangic": "22:00", "bitis": "07:00"}
    kayit = []
    k = kural(sessiz_saatler=ss, surekli_saat=3)
    isle(pred, k, tmp_path / "a.json", _kayit_gonderici(k, kayit))
    assert len(kayit) == 1 and "KRİTİK" in kayit[0]
    kayit2 = []
    k2 = kural(sessiz_saatler={**ss, "kritik_haric": False}, surekli_saat=3)
    r = isle(pred, k2, tmp_path / "b.json", _kayit_gonderici(k2, kayit2))
    assert kayit2 == [] and len(r.ertelenen) == 1


# --------------------------------------------------------------------------- deneme modu


def _tum_kanallar():
    return {
        "konsol": {"aktif": True},
        "eposta": {
            "aktif": True,
            "sunucu": "smtp.test",
            "gonderen": "a@test",
            "alicilar": ["b@test", "c@test"],
        },
        "telegram": {"aktif": True, "sohbet_idleri": ["111", "222"]},
        "webhook": {"aktif": True, "url": "https://hook.test/x?token=gizli"},
    }


def test_deneme_modu_hicbir_sey_gondermez(tmp_path):
    k = kural(kanallar=_tum_kanallar(), surekli_saat=3)
    assert k.deneme_modu is True  # varsayılan açık
    kayit = []
    g = Gonderici(k, cikti=kayit.append)
    durum = tmp_path / "durum.json"
    with (
        mock.patch("urllib.request.urlopen", side_effect=AssertionError("ağ çağrısı!")),
        mock.patch("smtplib.SMTP", side_effect=AssertionError("smtp çağrısı!")),
        mock.patch("smtplib.SMTP_SSL", side_effect=AssertionError("smtp çağrısı!")),
    ):
        r = isle(frame([N] * 2 + [K] * 4), k, durum, g)
    assert r.deneme and len(r.olaylar) == 1
    sonuc = r.sonuclar[0][1]
    assert {s.kanal for s in sonuc} == {"konsol", "eposta", "telegram", "webhook"}
    assert all(s.ok and s.deneme for s in sonuc)
    assert any("[DENEME] telegram" in x for x in kayit)
    assert "DENEME MODU" in r.ozet()
    assert not durum.exists()  # deneme durumu kaydetmez
    assert "token=gizli" not in "".join(kayit)  # webhook sorgusu yazdırılmaz


# --------------------------------------------------------------------------- webhook / imza


def test_imza_bilinen_vektor():
    govde = b"The quick brown fox jumps over the lazy dog"
    assert imza(govde, "key") == "sha256=f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8"


def _olay():
    return olaylari_uret(frame([K] * 4), Kurallar(), bildirim.bos_durum())[0]


def _yanit(kod=200):
    y = mock.MagicMock()
    y.status = kod
    y.__enter__.return_value = y
    return y


def test_webhook_imzasi_govdeyle_eslesir():
    k = kural(kanallar={"webhook": {"aktif": True, "url": "https://hook.test/x"}})
    g = Gonderici(k, deneme=False, ortam={bildirim.ENV_WEBHOOK_SIRRI: "sir123"}, uyku=lambda s: None)
    with mock.patch("urllib.request.urlopen", return_value=_yanit()) as up:
        sonuc = g.gonder(_olay())
    assert sonuc[0].ok and sonuc[0].deneme_sayisi == 1
    istek = up.call_args[0][0]
    beklenen = "sha256=" + hmac.new(b"sir123", istek.data, hashlib.sha256).hexdigest()
    assert istek.get_header("X-sogutma-imza") == beklenen
    assert istek.get_method() == "POST"
    govde = json.loads(istek.data.decode("utf-8"))
    assert govde["olay"]["durum"] == K and "KRİTİK" in govde["mesaj"]
    assert "sir123" not in istek.data.decode("utf-8")


def test_webhook_sirsiz_imza_basligi_yok():
    k = kural(kanallar={"webhook": {"aktif": True, "url": "https://hook.test/x"}})
    g = Gonderici(k, deneme=False, ortam={}, uyku=lambda s: None)
    with mock.patch("urllib.request.urlopen", return_value=_yanit()) as up:
        g.gonder(_olay())
    assert up.call_args[0][0].get_header("X-sogutma-imza") is None


# --------------------------------------------------------------------- telegram, e-posta, hata yönetimi


def test_telegram_gonderimi():
    k = kural(kanallar={"telegram": {"aktif": True, "sohbet_idleri": ["111", "222"]}})
    g = Gonderici(k, deneme=False, ortam={bildirim.ENV_TELEGRAM_TOKEN: "TOK:EN"}, uyku=lambda s: None)
    with mock.patch("urllib.request.urlopen", return_value=_yanit()) as up:
        sonuc = g.gonder(_olay())
    assert [s.ok for s in sonuc] == [True, True]
    istek = up.call_args_list[0][0][0]
    assert istek.full_url == "https://api.telegram.org/botTOK:EN/sendMessage"
    assert json.loads(istek.data)["chat_id"] == "111"
    assert "KRİTİK" in json.loads(istek.data)["text"]


def test_telegram_token_yoksa_hata_raporlanir():
    k = kural(kanallar={"telegram": {"aktif": True, "sohbet_idleri": ["1"]}})
    sonuc = Gonderici(k, deneme=False, ortam={}).gonder(_olay())
    assert not sonuc[0].ok and bildirim.ENV_TELEGRAM_TOKEN in sonuc[0].hata


def test_ag_hatasi_yeniden_denenir_ve_yakalanir():
    k = kural(kanallar={"telegram": {"aktif": True, "sohbet_idleri": ["1"]}})
    uyku = mock.Mock()
    g = Gonderici(k, deneme=False, ortam={bildirim.ENV_TELEGRAM_TOKEN: "TOKEN123"}, uyku=uyku, bekleme_s=1)
    hata = urllib.error.URLError("bağlantı https://api.telegram.org/botTOKEN123/sendMessage koptu")
    with mock.patch("urllib.request.urlopen", side_effect=hata) as up:
        sonuc = g.gonder(_olay())
    assert not sonuc[0].ok and sonuc[0].deneme_sayisi == 3 and up.call_count == 3
    assert [c.args[0] for c in uyku.call_args_list] == [1, 2]  # üstel bekleme
    assert "TOKEN123" not in sonuc[0].hata  # sırlar hata metnine sızmaz


def test_gecici_hata_sonra_basari():
    k = kural(kanallar={"webhook": {"aktif": True, "url": "https://hook.test/x"}})
    g = Gonderici(k, deneme=False, ortam={}, uyku=lambda s: None)
    with mock.patch("urllib.request.urlopen", side_effect=[TimeoutError("zaman aşımı"), _yanit()]):
        sonuc = g.gonder(_olay())
    assert sonuc[0].ok and sonuc[0].deneme_sayisi == 2


def test_http_4xx_tekrar_denenmez_5xx_denenir():
    k = kural(kanallar={"webhook": {"aktif": True, "url": "https://hook.test/x"}})
    g = Gonderici(k, deneme=False, ortam={}, uyku=lambda s: None)

    def http(kod):
        return urllib.error.HTTPError("https://hook.test/x", kod, "x", {}, None)

    with mock.patch("urllib.request.urlopen", side_effect=http(401)) as up:
        s = g.gonder(_olay())[0]
    assert not s.ok and up.call_count == 1 and "401" in s.hata
    with mock.patch("urllib.request.urlopen", side_effect=http(503)) as up:
        s = g.gonder(_olay())[0]
    assert not s.ok and up.call_count == 3


def test_eposta_starttls_ve_oturum():
    c = _tum_kanallar()["eposta"] | {"port": 587}
    k = kural(kanallar={"eposta": c})
    g = Gonderici(
        k, deneme=False, ortam={bildirim.ENV_SMTP_PAROLA: "p4rola", bildirim.ENV_SMTP_KULLANICI: "kul"}
    )
    with mock.patch("smtplib.SMTP") as smtp:
        sonuc = g.gonder(_olay())
    assert sonuc[0].ok
    smtp.assert_called_once()
    assert smtp.call_args.args[:2] == ("smtp.test", 587)
    s = smtp.return_value.__enter__.return_value
    s.starttls.assert_called_once()
    s.login.assert_called_once_with("kul", "p4rola")
    msg = s.send_message.call_args.args[0]
    assert msg["To"] == "b@test, c@test" and "KRİTİK" in msg["Subject"]
    assert "Öneri" in msg.get_content()


def test_eposta_hatalari():
    k = kural(kanallar={"eposta": _tum_kanallar()["eposta"]})
    g = Gonderici(
        k,
        deneme=False,
        ortam={bildirim.ENV_SMTP_PAROLA: "x", bildirim.ENV_SMTP_KULLANICI: "u"},
        uyku=lambda s: None,
    )
    with mock.patch("smtplib.SMTP", side_effect=ConnectionRefusedError("reddedildi")) as smtp:
        s = g.gonder(_olay())[0]
    assert not s.ok and smtp.call_count == 3
    with mock.patch("smtplib.SMTP") as smtp:
        smtp.return_value.__enter__.return_value.login.side_effect = smtplib.SMTPAuthenticationError(
            535, b"kotu"
        )
        s = g.gonder(_olay())[0]
    assert not s.ok and smtp.call_count == 1  # kimlik hatası yeniden denenmez
    # Parola tanımsız: ağa çıkmadan raporlanır
    g2 = Gonderici(k, deneme=False, ortam={bildirim.ENV_SMTP_KULLANICI: "u"})
    with mock.patch("smtplib.SMTP") as smtp:
        s = g2.gonder(_olay())[0]
    assert not s.ok and bildirim.ENV_SMTP_PAROLA in s.hata and smtp.call_count == 0


def test_kanal_cokmesi_digerlerini_etkilemez():
    k = kural(kanallar={"konsol": {"aktif": True}, "webhook": {"aktif": True, "url": "https://hook.test/x"}})
    kayit = []
    g = Gonderici(k, deneme=False, ortam={}, cikti=kayit.append, uyku=lambda s: None)
    with mock.patch.object(Gonderici, "_post", side_effect=RuntimeError("beklenmedik")):
        sonuc = g.gonder(_olay())
    assert [s.kanal for s in sonuc] == ["konsol", "webhook"]
    assert sonuc[0].ok and not sonuc[1].ok and "RuntimeError" in sonuc[1].hata and len(kayit) == 1


def test_basarisiz_olay_sonraki_calistirmada_yeniden_denenir(tmp_path):
    k = kural(kanallar={"webhook": {"aktif": True, "url": "https://hook.test/x"}}, surekli_saat=3)
    durum = tmp_path / "durum.json"
    pred = frame([N] * 2 + [K] * 4)
    g = Gonderici(k, deneme=False, ortam={}, uyku=lambda s: None)
    with mock.patch("urllib.request.urlopen", side_effect=OSError("ağ yok")):
        r = isle(pred, k, durum, g)
    assert len(r.basarisiz) == 1 and "gönderilemedi" in r.ozet()
    assert json.loads(durum.read_text())["bekleyen"][0]["tekrar"] == 1
    with mock.patch("urllib.request.urlopen", return_value=_yanit()) as up:
        r2 = isle(pred, k, durum, g)
    assert len(r2.olaylar) == 1 and r2.basarisiz == [] and up.call_count == 1
    assert json.loads(durum.read_text())["bekleyen"] == []
    # Her seferinde başarısız olursa MAX_TEKRAR sonunda vazgeçilir
    durum2 = tmp_path / "durum2.json"
    with mock.patch("urllib.request.urlopen", side_effect=OSError("ağ yok")):
        for _ in range(bildirim.MAX_TEKRAR):
            r3 = isle(pred, k, durum2, g)
    assert len(r3.vazgecilen) == 1 and json.loads(durum2.read_text())["bekleyen"] == []


# --------------------------------------------------------------------------- durum dosyası


def test_durum_kalicidir_tekrar_gonderilmez(tmp_path):
    k = kural(surekli_saat=3)
    durum = tmp_path / "alt" / "durum.json"
    kayit = []
    pred = frame([N] * 3 + [I] * 6)
    r1 = isle(pred, k, durum, _kayit_gonderici(k, kayit))
    assert len(r1.olaylar) == 1 and len(kayit) == 1 and durum.exists()
    r2 = isle(pred, k, durum, _kayit_gonderici(k, kayit))  # aynı veriyle saatlik cron tekrarı
    assert r2.olaylar == [] and len(kayit) == 1 and "yeni olay yok" in r2.ozet()
    # Yeni saatler eklenir: Kritik'e yükselme yalnızca yeni olay olarak gelir
    uzun = pd.concat([pred, frame([K] * 4, baslangic="2026-03-01 09:00")], ignore_index=True)
    r3 = isle(uzun, k, durum, _kayit_gonderici(k, kayit))
    assert turler(r3.olaylar) == ["yukselis"] and len(kayit) == 2


def test_bozuk_durum_dosyasi_yedeklenir(tmp_path):
    durum = tmp_path / "durum.json"
    durum.write_text("{bozuk", encoding="utf-8")
    k = kural(surekli_saat=3)
    r = isle(frame([K] * 5), k, durum, _kayit_gonderici(k, []))
    assert r.uyarilar and (tmp_path / "durum.json.bozuk").exists() and len(r.olaylar) == 1


# --------------------------------------------------------------------------- mesajlar


def test_mesaj_sablonlari():
    k = kural(birim_adlari={"A1": "Soğuk Oda A1"})
    o = olaylari_uret(frame([K] * 4, guven=0.97, eta=48.0), k, bildirim.bos_durum())[0]
    kisa = kisa_mesaj(o)
    assert kisa.startswith("🔴 KRİTİK · Soğuk Oda A1 · Soğutucu gaz kaçağı (güven %97)")
    assert "tahmini arıza ~2 gün" in kisa and "Öneri: " in kisa and "\n" not in kisa
    uzun = uzun_mesaj(o)
    assert "Sağlık skoru" in uzun and "Belirtiler" in uzun and "\n" in uzun
    iyi = olaylari_uret(frame([K] * 4 + [N] * 8), k, bildirim.bos_durum())[-1]
    assert kisa_mesaj(iyi).startswith("🟢 NORMALE DÖNDÜ · Soğuk Oda A1")
    assert bildirim.eta_metni(5) == "~5 saat" and bildirim.eta_metni(30) == "~1 gün 6 saat"
    assert bildirim.eta_metni(None) == "-"


# --------------------------------------------------------------------------- ayar dosyası


def test_ornek_ayar_yuklenir_ve_sir_icermez():
    k = ayar_yukle(ORNEK_AYAR)
    assert k.deneme_modu is True and k.cooldown_saat == 12 and k.surekli_saat == {"İzlemede": 6, "Kritik": 3}
    assert k.kanallar["konsol"]["aktif"] and not k.kanallar["eposta"]["aktif"]
    ham = ORNEK_AYAR.read_text(encoding="utf-8").lower()
    assert not any(a in ham for a in ('parola"', "password", 'token"', 'sirri"'))


def test_ayar_dogrulama():
    with pytest.raises(AyarHatasi, match="Bilinmeyen ayar"):
        Kurallar.from_dict({"cooldown": 3})
    with pytest.raises(AyarHatasi, match="min_guven"):
        Kurallar.from_dict({"min_guven": 1.5})
    with pytest.raises(AyarHatasi, match="alicilar"):
        Kurallar.from_dict({"kanallar": {"eposta": {"aktif": True, "sunucu": "s", "gonderen": "g"}}})
    with pytest.raises(AyarHatasi, match="sohbet_idleri"):
        Kurallar.from_dict({"kanallar": {"telegram": {"aktif": True}}})
    with pytest.raises(AyarHatasi, match="Bilinmeyen kanal"):
        Kurallar.from_dict({"kanallar": {"sms": {"aktif": True}}})
    with pytest.raises(AyarHatasi, match="SS:DD"):
        Kurallar.from_dict({"sessiz_saatler": {"aktif": True, "baslangic": "25:00"}})
    with pytest.raises(AyarHatasi, match="Ayar dosyası bulunamadı"):
        ayar_yukle("/yok/yok.json")
    assert Kurallar.from_dict({"surekli_saat": 4}).surekli_saat == {"İzlemede": 4, "Kritik": 4}


def test_bozuk_json_ayar(tmp_path):
    p = tmp_path / "a.json"
    p.write_text("{", encoding="utf-8")
    with pytest.raises(AyarHatasi, match="okunamadı"):
        ayar_yukle(p)


# --------------------------------------------------------------------------- predict.py


def _ayar(tmp_path, **kw):
    ayar = {"deneme_modu": False, "en_eski_olay_saat": None, "kanallar": {"konsol": {"aktif": True}}, **kw}
    p = tmp_path / "ayar.json"
    p.write_text(json.dumps(ayar), encoding="utf-8")
    return p


def _cli(trained_root, tmp_path, *ek):
    predict.main(
        [
            str(ORNEK_CSV),
            "-o",
            str(tmp_path / "rapor.csv"),
            "--model",
            str(trained_root / "models" / "predictor.joblib"),
            *ek,
        ]
    )


def test_cli_bildirimsiz_cikti_degismez(trained_root, tmp_path, capsys):
    _cli(trained_root, tmp_path)
    assert "Bildirim" not in capsys.readouterr().out


def test_cli_deneme_modu_gonderim_yapmaz(trained_root, tmp_path, capsys):
    durum = tmp_path / "durum.json"
    ayar = _ayar(tmp_path, kanallar=_tum_kanallar())  # config deneme_modu=false derse bile --gonder yok
    with (
        mock.patch("urllib.request.urlopen", side_effect=AssertionError("ağ!")),
        mock.patch("smtplib.SMTP", side_effect=AssertionError("smtp!")),
    ):
        _cli(trained_root, tmp_path, "--bildirim", str(ayar), "--durum", str(durum))
    out = capsys.readouterr().out
    assert "DENEME MODU" in out and "[DENEME] telegram" in out
    assert not durum.exists()


def test_cli_gonder_durumu_yazar_ve_tekrar_gondermez(trained_root, tmp_path, capsys):
    durum = tmp_path / "durum.json"
    ayar = _ayar(tmp_path, birim_adlari={"U1": "Soğuk Oda A1"})
    _cli(trained_root, tmp_path, "--bildirim", str(ayar), "--gonder", "--durum", str(durum))
    out = capsys.readouterr().out
    assert "gönderim açık" in out and "Soğuk Oda A1" in out and durum.exists()
    _cli(trained_root, tmp_path, "--bildirim", str(ayar), "--gonder", "--durum", str(durum))
    assert "yeni olay yok" in capsys.readouterr().out


def test_cli_gonder_ayarsiz_ve_bozuk_ayar(trained_root, tmp_path):
    with pytest.raises(SystemExit):
        _cli(trained_root, tmp_path, "--gonder")
    kotu = tmp_path / "kotu.json"
    kotu.write_text('{"min_guven": 5}', encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        _cli(trained_root, tmp_path, "--bildirim", str(kotu))
    assert "min_guven" in str(e.value)
