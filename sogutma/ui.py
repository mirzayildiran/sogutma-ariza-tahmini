"""Panel sayfalarının ortak yardımcıları: veri/model yükleme, biçimlendirme, ortak grafikler."""

import json
import os
from pathlib import Path

import joblib
import pandas as pd
import plotly.express as px
import streamlit as st
from plotly.subplots import make_subplots

from .simulator import START

STATUS_STYLE = {"Normal": ("🟢", "#2e9e5b"), "İzlemede": ("🟠", "#e08a1e"), "Kritik": ("🔴", "#d0342c")}

# Çizilecek sinyaller: (alt grafik satırı, ham sütun, ad)
SINYALLER = [(1, "t_room", "Oda"), (1, "t_amb", "Dış ortam"), (2, "p_suc", "Emme"), (2, "p_dis", "Basma"),
             (3, "sh", "Kızgınlık"), (3, "sc", "Aşırı soğutma"), (4, "i_comp", "Kompresör akımı"),
             (5, "vib", "Titreşim"), (5, "i_fan", "Fan akımı")]
SADECE_CALISIRKEN = ["p_suc", "p_dis", "sh", "sc", "i_comp", "vib", "i_fan"]


def root() -> Path:
    """Veri/model klasörü. SOGUTMA_ROOT değişkeni bunu değiştirir (testler için)."""
    return Path(os.environ.get("SOGUTMA_ROOT") or Path(__file__).resolve().parent.parent)


def ensure_trained():
    """İlk çalıştırmada (ör. Streamlit Cloud) veri ve model yoksa üretir."""
    r = root()
    if not (r / "models/predictor.joblib").exists():
        import train

        with st.spinner("İlk çalıştırma: sentetik veri üretiliyor ve model eğitiliyor (~30 sn)..."):
            train.main(out_root=r)


@st.cache_resource
def load_demo(root_dir):
    r = Path(root_dir)
    raw = pd.read_parquet(r / "data/demo_raw.parquet")
    hourly = pd.read_parquet(r / "data/demo_hourly.parquet")
    units = json.loads((r / "data/demo_units.json").read_text())
    metrics = json.loads((r / "models/metrics.json").read_text())
    model = joblib.load(r / "models/predictor.joblib")
    return raw, hourly, units, metrics, model


@st.cache_resource
def load_model(root_dir):
    return joblib.load(Path(root_dir) / "models/predictor.joblib")


def load_metrics(root_dir):
    """Eğitim metrikleri; dosya yoksa ya da okunamazsa None."""
    try:
        return json.loads((Path(root_dir) / "models/metrics.json").read_text())
    except (OSError, ValueError):
        return None


def fmt_eta(h):
    if h is None or pd.isna(h):
        return "—"
    h = int(h)
    d, r = divmod(h, 24)
    return f"~{d} gün {r} saat" if d else f"~{r} saat"


def fmt_time(ts):
    day = (ts - START).days + 1
    return f"Gün {day}, {ts:%H:%M}"


def fmt_tl(v):
    """1234567 → '1.234.567 TL' (Türkçe binlik ayracı)."""
    return f"{v:,.0f}".replace(",", ".") + " TL"


def saglik_grafigi(df, height=340):
    """Ünite başına sağlık skoru çizgileri ve Normal / İzlemede / Kritik bantları."""
    fig = px.line(df, x="timestamp", y="health", color="unit_id",
                  labels={"timestamp": "", "health": "Sağlık skoru", "unit_id": "Ünite"})
    fig.add_hrect(y0=75, y1=100, fillcolor="green", opacity=0.05, line_width=0)
    fig.add_hrect(y0=50, y1=75, fillcolor="orange", opacity=0.06, line_width=0)
    fig.add_hrect(y0=0, y1=50, fillcolor="red", opacity=0.05, line_width=0)
    fig.update_layout(height=height, margin=dict(t=10, b=10), yaxis_range=[0, 102])
    return fig


def ornekleme_araligi(gun):
    """Grafik aralığına göre yeniden örnekleme sıklığı (None: 5 dakikalık ham veri)."""
    if gun <= 1:
        return None
    return "15min" if gun <= 3 else "30min" if gun <= 7 else "1h" if gun <= 14 else "2h"


def sinyal_grafigi(rw, hw, setpoint, color, gun, isaretler=()):
    """Bir ünitenin sinyal grafikleri.

    rw: ünitenin ham (5 dk) verisi, yalnızca gösterilecek aralık; hw: aynı aralıkta saatlik
    sağlık skoru (timestamp, health); isaretler: [(zaman, etiket)] dikey kırmızı çizgiler.
    Döndürür: (şekil, yeniden örnekleme sıklığı).
    """
    running = rw["comp_on"] & ~rw["defrost"]
    sig = rw.set_index("timestamp")[["t_room", "t_amb"]].copy()
    for c in SADECE_CALISIRKEN:
        # Kompresör dururken bu sinyaller anlamsız: yalnızca çalışma anları
        sig[c] = rw[c].where(running).to_numpy()
    freq = ornekleme_araligi(gun)
    if freq:
        sig = sig.resample(freq).mean()

    fig = make_subplots(rows=6, cols=1, shared_xaxes=True, vertical_spacing=0.04,
                        subplot_titles=["Sıcaklık (°C)", "Emme / basma basıncı (bar)",
                                        "Kızgınlık / aşırı soğutma (K)", "Kompresör akımı (A)",
                                        "Titreşim (mm/s) / fan akımı (A)", "Sağlık skoru"])
    for row_i, col, name in SINYALLER:
        if sig[col].notna().any():  # eksik sensör (tamamen boş sütun) çizilmez
            fig.add_scatter(x=sig.index, y=sig[col], name=name, line=dict(width=1.3),
                            connectgaps=freq is not None, row=row_i, col=1)
    fig.add_hline(y=setpoint, line_dash="dot", line_color="gray", row=1, col=1)
    fig.add_scatter(x=hw["timestamp"], y=hw["health"], name="Sağlık", line=dict(width=2, color=color),
                    row=6, col=1)
    fig.update_yaxes(range=[0, 102], row=6, col=1)
    for ts, label in isaretler:
        fig.add_vline(x=ts, line_dash="dash", line_color="red")
        fig.add_annotation(x=ts, y=1, yref="paper", text=label, showarrow=False,
                           font=dict(color="red"), xanchor="left")
    fig.update_layout(height=1150, margin=dict(t=30, b=10),
                      legend=dict(orientation="h", yanchor="top", y=-0.03))
    return fig, freq


def sinyal_notu(freq):
    return ("Grafikler " + ("5 dakikalık ham veriyi" if freq is None else f"{freq} ortalamalarını")
            + " gösterir; basınç, akım gibi değerler yalnızca kompresör çalışırken alınır.")
