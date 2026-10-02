"""Panel duman testi: veri/model geçici klasörden okunur (SOGUTMA_ROOT), 120 ünitelik eğitim yapılmaz."""

import json
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from sogutma.simulator import START

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture(scope="module")
def app_root(trained_root):
    mp = pytest.MonkeyPatch()
    mp.setenv("SOGUTMA_ROOT", str(trained_root))
    yield trained_root
    mp.undo()


@pytest.fixture
def at(app_root):
    return AppTest.from_file(APP, default_timeout=120).run()


def counts(at):
    return [int(m.value) for m in at.metric[:4]]


def test_app_runs_without_exception(at):
    assert not at.exception
    total, normal, watch, critical = counts(at)
    assert total == 12
    assert normal + watch + critical == total
    assert len(at.tabs) == 5
    assert at.title[0].value.endswith("Soğutma Arıza Tahmini")


def test_time_slider_moves(at):
    slider = at.slider[0]
    lo, hi = slider.min, slider.max
    assert hi > lo and lo <= slider.value <= hi

    at.slider[0].set_value(lo).run()
    assert not at.exception
    assert at.session_state["t"] == lo
    ts = START + pd.Timedelta(hours=lo)
    assert any(f"Gün {(ts - START).days + 1}, {ts:%H:%M}" in m.value for m in at.sidebar.markdown)
    assert counts(at)[0] == 12
    early = counts(at)

    at.slider[0].set_value(hi).run()
    assert not at.exception
    assert at.session_state["t"] == hi
    late = counts(at)
    assert late[0] == 12 and sum(late[1:]) == 12
    # Başlangıçta ünite sağlıklı; zaman ilerleyince uyarılar artmalı
    assert early[1] == 12
    assert late[2] + late[3] > 0


def test_toggles_and_chart_range(at):
    at.toggle[1].set_value(True).run()  # gerçek arıza bilgisini göster
    assert not at.exception
    assert any("Gerçek" in c.value for c in at.caption)
    at.radio[0].set_value(30).run()
    assert not at.exception


# ---------------------------------------------------------------- Çok sayfalı yapı
ANALIZ, MALIYET = "sayfalar/analiz.py", "sayfalar/maliyet.py"


def test_filo_state_survives_page_switch(at):
    at.slider[0].set_value(at.slider[0].min + 100).run()
    at.switch_page(MALIYET).run()
    assert not at.exception
    at.switch_page("sayfalar/filo.py").run()
    assert not at.exception
    assert at.slider[0].value == at.slider[0].min + 100


def test_analysis_page_empty_state(at):
    at.switch_page(ANALIZ).run()
    assert not at.exception
    assert any("R404A" in w.value for w in at.warning)
    assert any("Örnek veriyi kullan" in i.value for i in at.info)
    assert not at.metric  # veri yokken sonuç gösterilmez


def test_analysis_page_on_example_csv(at):
    at.switch_page(ANALIZ).run()
    at.button[0].click().run()
    assert not at.exception
    assert any("ornek_veri.csv" in s.value for s in at.success)
    assert [m.label for m in at.metric] == ["Sağlık skoru", "Durum", "Olası arıza", "Tahmini kalan süre"]
    assert len(at.get("download_button")) == 1
    assert any(w.value.startswith("**Sınırlama:**") for w in at.warning)
    # Ayarlar değişince analiz kaybolmaz ve yeniden çalışır
    at.checkbox[1].check().run()  # gauge
    assert not at.exception
    assert len(at.metric) == 4


def test_analysis_page_setpoint_option(at):
    at.switch_page(ANALIZ).run()
    at.button[0].click().run()
    at.checkbox[0].check().run()  # set değerini kendim gireyim
    at.number_input[0].set_value(3.0).run()
    assert not at.exception
    assert len(at.metric) == 4


def test_analysis_page_shows_turkish_error_for_bad_csv(at):
    at.switch_page(ANALIZ).run()
    at.session_state["analiz_kaynak"] = ("bozuk.csv", b"a;b\n1;2\n")
    at.run()
    assert not at.exception
    assert any("Veri dosyası kullanılamıyor" in e.value and "Zorunlu sütun" in e.value for e in at.error)
    assert not at.metric


def test_roi_page_defaults_and_math(at):
    at.switch_page(MALIYET).run()
    assert not at.exception
    # Örnek değerler açıkça işaretli olmalı
    assert any("örnek yer tutucu" in w.value for w in at.warning)

    at.number_input(key="roi_unite").set_value(10)
    at.number_input(key="roi_sikilik").set_value(0.5)
    at.number_input(key="roi_acil").set_value(15000)
    at.number_input(key="roi_urun").set_value(40000)
    at.slider(key="roi_oran").set_value(80)
    at.slider(key="roi_onarim").set_value(40)
    at.slider(key="roi_koruma").set_value(80)
    at.number_input(key="roi_aylik").set_value(300)
    at.number_input(key="roi_kurulum").set_value(6000).run()
    assert not at.exception
    vals = {m.label: m.value for m in at.metric}
    assert vals["Yıllık önlenen maliyet"] == "152.000 TL"
    assert vals["Yıllık sistem maliyeti"] == "36.000 TL"
    assert vals["Yıllık net fayda"] == "116.000 TL"
    assert vals["Geri ödeme süresi"] == "6.2 ay"


def test_roi_page_no_payback_message(at):
    at.switch_page(MALIYET).run()
    at.slider(key="roi_oran").set_value(0).run()
    assert not at.exception
    assert {m.label: m.value for m in at.metric}["Geri ödeme süresi"] == "Geri ödenmiyor"
    assert any("negatif" in e.value for e in at.error)


# ---------------------------------------------------------------- Bildirim ayarları sayfası
BILDIRIM = "sayfalar/bildirim.py"
GIZLI = "cok-gizli-parola-123"


def bildirim_sayfasi(at):
    at.switch_page(BILDIRIM).run()
    assert not at.exception
    return at


def kod_metinleri(at):
    return [c.value for c in at.code]


def test_filo_alerts_tab_links_to_notification_page(at):
    assert any(link.proto.page == "bildirim" for link in at.get("page_link"))


def test_notification_page_renders_with_valid_defaults(at):
    bildirim_sayfasi(at)
    assert at.title[0].value.endswith("Bildirim Ayarları")
    assert any(s.value == "Ayarlar geçerli." for s in at.success)
    assert not at.error
    indir = at.get("download_button")
    assert len(indir) == 1 and not indir[0].proto.disabled
    # Varsayılan ayar JSON'u modülün şemasına uygun
    assert any('"deneme_modu": true' in k for k in kod_metinleri(at))


def test_notification_page_shows_env_status_without_values(at, monkeypatch):
    monkeypatch.setenv("SOGUTMA_SMTP_PAROLA", GIZLI)
    monkeypatch.delenv("SOGUTMA_TELEGRAM_TOKEN", raising=False)
    bildirim_sayfasi(at)
    tablo = at.dataframe[0].value.set_index("Değişken")["Durum"]
    assert tablo["SOGUTMA_SMTP_PAROLA"] == "✅ tanımlı"
    assert tablo["SOGUTMA_TELEGRAM_TOKEN"] == "⚪ tanımlı değil"
    assert len(tablo) == 5
    # Değer hiçbir yerde görünmez (tablolar, JSON, metinler)
    assert GIZLI not in str(at.dataframe[0].value) + " ".join(kod_metinleri(at))
    assert not any(GIZLI in m.value for m in at.markdown)
    # Etkin kanalda eksik değişken uyarısı
    at.checkbox(key="bd_telegram").check().run()
    assert any("SOGUTMA_TELEGRAM_TOKEN" in w.value and "tanımlı değil" in w.value for w in at.warning)


def test_notification_page_validation_errors_in_turkish(at):
    bildirim_sayfasi(at)
    at.checkbox(key="bd_eposta").check().run()  # sunucu / gönderen / alıcılar boş
    assert not at.exception
    assert any("Ayarlar geçersiz" in e.value and "kanallar.eposta" in e.value and "alicilar" in e.value
               for e in at.error)
    assert at.get("download_button")[0].proto.disabled
    assert not at.metric  # geçersiz ayarla önizleme çalışmaz

    at.checkbox(key="bd_eposta").uncheck()
    at.checkbox(key="bd_sessiz").check().run()
    at.time_input(key="bd_sessiz_bas").set_value(at.time_input(key="bd_sessiz_bit").value).run()
    assert any("başlangıç ve bitiş aynı olamaz" in e.value for e in at.error)

    at.checkbox(key="bd_sessiz").uncheck()
    at.checkbox(key="bd_webhook").check().run()
    at.text_input(key="bd_wh_url").set_value("ftp://example.com").run()
    assert any("http:// ya da https://" in e.value for e in at.error)

    at.text_input(key="bd_wh_url").set_value("https://example.com/hook").run()
    assert not at.error and at.success[0].value == "Ayarlar geçerli."


def test_notification_preview_renders_messages_for_demo_fleet(at):
    at.slider[0].set_value(at.slider[0].max).run()  # Filo sayfasında zamanı sona al
    bildirim_sayfasi(at)
    olay, mesaj = (int(m.value) for m in at.metric[:2])
    assert olay > 0 and mesaj == olay  # tek kanal: konsol
    assert any("KRİTİK" in k or "İZLEMEDE" in k for k in kod_metinleri(at))
    tablo = at.dataframe[-1].value
    assert len(tablo) == olay and set(tablo["Sonuç"]) == {"gönderilirdi"}

    # E-posta ve Telegram da açılınca olay başına kanal sayısı kadar mesaj
    at.checkbox(key="bd_eposta").check().run()
    at.text_input(key="bd_smtp_sunucu").set_value("smtp.example.com")
    at.text_input(key="bd_smtp_gonderen").set_value("izleme@example.com")
    at.text_area(key="bd_smtp_alicilar").set_value("bakim@example.com").run()
    at.checkbox(key="bd_telegram").check().run()
    at.text_area(key="bd_tg_idler").set_value("-1001234567890").run()
    assert not at.exception and not at.error
    assert int(at.metric[1].value) == 3 * int(at.metric[0].value)
    kod = kod_metinleri(at)
    assert any(k.startswith("Konu: [Soğutma]") for k in kod)  # e-posta
    assert any("Belirtiler" in k for k in kod)  # uzun e-posta gövdesi


def test_notification_preview_follows_filo_time(at):
    at.slider[0].set_value(at.slider[0].min).run()
    bildirim_sayfasi(at)
    assert int(at.metric[0].value) == 0
    assert any("bildirim üretilmedi" in i.value for i in at.info)
    ts = START + pd.Timedelta(hours=at.session_state["_t_son"])
    assert any(f"Gün {(ts - START).days + 1}, {ts:%H:%M}" in c.value for c in at.caption)


def test_notification_page_never_sends_or_writes_state(at, app_root, monkeypatch):
    import smtplib
    import socket
    import urllib.request

    from sogutma import bildirim

    def yasak(*a, **k):
        raise AssertionError("Önizleme ağa bağlanmamalı / durum yazmamalı")

    monkeypatch.setattr(smtplib.SMTP, "__init__", yasak)
    monkeypatch.setattr(smtplib.SMTP_SSL, "__init__", yasak)
    monkeypatch.setattr(urllib.request, "urlopen", yasak)
    monkeypatch.setattr(socket.socket, "connect", yasak)
    monkeypatch.setattr(bildirim.Gonderici, "_post", yasak)
    monkeypatch.setattr(bildirim, "durum_kaydet", yasak)
    monkeypatch.setenv("SOGUTMA_SMTP_PAROLA", GIZLI)
    monkeypatch.setenv("SOGUTMA_TELEGRAM_TOKEN", GIZLI)
    onceki = set(app_root.rglob("*"))

    at.slider[0].set_value(at.slider[0].max).run()
    bildirim_sayfasi(at)
    at.checkbox(key="bd_eposta").check().run()
    at.text_input(key="bd_smtp_sunucu").set_value("smtp.example.com")
    at.text_input(key="bd_smtp_kullanici").set_value("uyari@example.com")
    at.text_input(key="bd_smtp_gonderen").set_value("izleme@example.com")
    at.text_area(key="bd_smtp_alicilar").set_value("bakim@example.com").run()
    at.checkbox(key="bd_telegram").check().run()
    at.text_area(key="bd_tg_idler").set_value("123").run()
    at.checkbox(key="bd_webhook").check().run()
    at.text_input(key="bd_wh_url").set_value("https://example.com/hook").run()
    assert not at.exception and not at.error
    assert int(at.metric[1].value) == 4 * int(at.metric[0].value) > 0
    assert set(app_root.rglob("*")) == onceki  # durum dosyası ya da başka dosya yazılmadı
    assert GIZLI not in " ".join(kod_metinleri(at))


def test_notification_page_loads_uploaded_config_and_drops_secrets(at):
    ayar = {
        "deneme_modu": False, "bildirilen_durumlar": ["Kritik"], "cooldown_saat": 24, "min_guven": 0.8,
        "surekli_saat": {"İzlemede": 8, "Kritik": 2},
        "sessiz_saatler": {"aktif": True, "baslangic": "23:30", "bitis": "06:15", "kritik_haric": False},
        "birim_adlari": {"U1": "Soğuk Oda A1"},
        "kanallar": {"konsol": {"aktif": False}, "eposta": {
            "aktif": True, "sunucu": "smtp.example.com", "port": 465, "guvenlik": "ssl",
            "gonderen": "izleme@example.com", "alicilar": ["a@example.com", "b@example.com"],
            "parola": GIZLI}},
    }
    bildirim_sayfasi(at)
    at.session_state["bd_kaynak"] = ("ayar.json", json.dumps(ayar).encode("utf-8"))
    at.session_state["bd_uygula"] = True
    at.run()
    assert not at.exception
    assert at.multiselect(key="bd_durumlar").value == ["Kritik"]
    assert at.number_input(key="bd_cooldown").value == 24
    assert at.slider(key="bd_min_guven").value == 80
    assert at.number_input(key="bd_surekli_izlemede").value == 8
    assert at.checkbox(key="bd_sessiz").value and not at.checkbox(key="bd_konsol").value
    assert at.time_input(key="bd_sessiz_bas").value.strftime("%H:%M") == "23:30"
    assert at.text_input(key="bd_smtp_sunucu").value == "smtp.example.com"
    assert at.selectbox(key="bd_smtp_guv").value == "ssl"
    assert at.text_area(key="bd_smtp_alicilar").value == "a@example.com\nb@example.com"
    assert any("parola alanı yok sayıldı" in w.value for w in at.warning)
    assert any("deneme_modu" in w.value for w in at.warning)
    assert not at.error
    cikti = " ".join(kod_metinleri(at))
    assert GIZLI not in cikti and '"deneme_modu": true' in cikti and "Soğuk Oda A1" in cikti


def test_notification_page_rejects_bad_uploaded_config(at):
    bildirim_sayfasi(at)
    at.session_state["bd_kaynak"] = ("bozuk.json", b'{"cooldown_saat": -5}')
    at.session_state["bd_uygula"] = True
    at.run()
    assert not at.exception
    assert any("Ayar dosyası kullanılamıyor" in e.value and "cooldown_saat" in e.value for e in at.error)
    at.session_state["bd_kaynak"] = ("bozuk2.json", b"{degil json")
    at.session_state["bd_uygula"] = True
    at.run()
    assert not at.exception
    assert any("Ayar dosyası kullanılamıyor" in e.value for e in at.error)


def test_notification_form_state_survives_page_switch(at):
    bildirim_sayfasi(at)
    at.number_input(key="bd_cooldown").set_value(30).run()
    at.switch_page(MALIYET).run()
    at.switch_page(BILDIRIM).run()
    assert not at.exception
    assert at.number_input(key="bd_cooldown").value == 30
