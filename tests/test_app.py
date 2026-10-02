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
    assert total == 8
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
    assert counts(at)[0] == 8
    early = counts(at)

    at.slider[0].set_value(hi).run()
    assert not at.exception
    assert at.session_state["t"] == hi
    late = counts(at)
    assert late[0] == 8 and sum(late[1:]) == 8
    # Başlangıçta ünite sağlıklı; zaman ilerleyince uyarılar artmalı
    assert early[1] == 8
    assert late[2] + late[3] > 0


def test_toggles_and_chart_range(at):
    at.toggle[1].set_value(True).run()  # gerçek arıza bilgisini göster
    assert not at.exception
    assert any("Gerçek" in c.value for c in at.caption)
    at.radio[0].set_value(30).run()
    assert not at.exception
