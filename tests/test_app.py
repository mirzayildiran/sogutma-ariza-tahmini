"""Panel duman testi: veri/model geçici klasörden okunur (SOGUTMA_ROOT), 120 ünitelik eğitim yapılmaz."""

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
