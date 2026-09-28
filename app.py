"""Soğutma arıza tahmini — izleme paneli (demo).

Çalıştırma:  streamlit run app.py
"""

import json
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from sogutma.faults import FAULT_TYPES, FAULTS, fault_name, short_name
from sogutma.simulator import START

ROOT = Path(__file__).parent
STATUS_STYLE = {"Normal": ("🟢", "#2e9e5b"), "İzlemede": ("🟠", "#e08a1e"), "Kritik": ("🔴", "#d0342c")}

st.set_page_config(page_title="Soğutma Arıza Tahmini", page_icon="❄️", layout="wide")


@st.cache_resource
def load():
    raw = pd.read_parquet(ROOT / "data/demo_raw.parquet")
    hourly = pd.read_parquet(ROOT / "data/demo_hourly.parquet")
    units = json.loads((ROOT / "data/demo_units.json").read_text())
    metrics = json.loads((ROOT / "models/metrics.json").read_text())
    model = joblib.load(ROOT / "models/predictor.joblib")
    return raw, hourly, units, metrics, model


if not (ROOT / "models/predictor.joblib").exists():
    # İlk çalıştırma (ör. Streamlit Cloud): veri ve model yoksa burada üretilir
    import train

    with st.spinner("İlk çalıştırma: sentetik veri üretiliyor ve model eğitiliyor (~30 sn)..."):
        train.main()

raw, hourly, units, metrics, model = load()
unit_by_id = {u["unit_id"]: u for u in units}
min_h = int((hourly["timestamp"].min() - START) / pd.Timedelta(hours=1))
max_h = int((hourly["timestamp"].max() - START) / pd.Timedelta(hours=1))


def fmt_eta(h):
    if h is None or pd.isna(h):
        return "—"
    h = int(h)
    d, r = divmod(h, 24)
    return f"~{d} gün {r} saat" if d else f"~{r} saat"


def fmt_time(ts):
    day = (ts - START).days + 1
    return f"Gün {day}, {ts:%H:%M}"


# ---------------------------------------------------------------- Kenar çubuğu
if "_next_t" in st.session_state:
    st.session_state["t"] = st.session_state.pop("_next_t")

with st.sidebar:
    st.title("❄️ Soğutma Arıza Tahmini")
    st.caption("Demo prototip · sentetik veri")
    if "t" not in st.session_state:
        # Paylaşılabilir bağlantı için başlangıç zamanı: ?t=<saat>
        start_t = int(st.query_params.get("t", min_h + 24 * 13))
        st.session_state["t"] = min(max(start_t, min_h), max_h)
    t_now = st.slider("Simülasyon zamanı (saat)", min_h, max_h, step=1, key="t")
    now = START + pd.Timedelta(hours=t_now)
    st.markdown(f"**{fmt_time(now)}**")
    play = st.toggle("▶ Canlı oynat", help="Zamanı otomatik ilerletir (her adımda 3 saat)")
    speed = st.select_slider("Oynatma hızı", ["Yavaş", "Normal", "Hızlı"], value="Normal")
    st.divider()
    show_truth = st.toggle("Gerçek arıza bilgisini göster",
                           help="Simülasyonda enjekte edilen gerçek arızayı ve arıza anını gösterir "
                                "(yalnızca demo amaçlı; gerçek sahada bu bilgi yoktur).")

cur = hourly[hourly["timestamp"] <= now]
latest = cur.groupby("unit_id").tail(1).set_index("unit_id")

tab_fleet, tab_unit, tab_alerts, tab_perf, tab_about = st.tabs(
    ["🏭 Filo Genel Bakış", "🔍 Ünite Detayı", "🚨 Uyarılar", "📊 Model Performansı", "ℹ️ Hakkında"])

# ---------------------------------------------------------------- Filo
with tab_fleet:
    counts = latest["status"].value_counts()
    c = st.columns(4)
    c[0].metric("Toplam ünite", len(latest))
    c[1].metric("🟢 Normal", int(counts.get("Normal", 0)))
    c[2].metric("🟠 İzlemede", int(counts.get("İzlemede", 0)))
    c[3].metric("🔴 Kritik", int(counts.get("Kritik", 0)))

    cols = st.columns(4)
    for i, (uid, r) in enumerate(latest.iterrows()):
        u = unit_by_id[uid]
        icon, color = STATUS_STYLE[r["status"]]
        with cols[i % 4].container(border=True):
            st.markdown(f"**{u['name']}**")
            st.markdown(
                f"<span style='font-size:2rem;font-weight:700;color:{color}'>{r['health']:.0f}</span>"
                f"<span style='color:gray'> / 100 sağlık</span>", unsafe_allow_html=True)
            st.markdown(f"{icon} {r['status']}")
            if r["pred_fault"] != "normal":
                st.markdown(f"**{fault_name(r['pred_fault'])}**  \n"
                            f"Güven: %{r['confidence'] * 100:.0f} · Tahmini arıza: {fmt_eta(r['eta_h'])}")
            else:
                st.markdown("Anormal durum yok  \n&nbsp;")
            st.caption(f"Oda: {r['t_room_dev'] + u['setpoint']:.1f} °C (hedef {u['setpoint']:.1f} °C)")
            if show_truth:
                ft = u["fault"]
                if ft == "normal":
                    st.caption("🎯 Gerçek: arıza yok")
                else:
                    fail_ts = START + pd.Timedelta(hours=u["failure_h"])
                    st.caption(f"🎯 Gerçek: {fault_name(ft)} · arıza anı {fmt_time(fail_ts)}")

    st.subheader("Sağlık skoru geçmişi")
    fig = px.line(cur, x="timestamp", y="health", color="unit_id",
                  labels={"timestamp": "", "health": "Sağlık skoru", "unit_id": "Ünite"})
    fig.add_hrect(y0=75, y1=100, fillcolor="green", opacity=0.05, line_width=0)
    fig.add_hrect(y0=50, y1=75, fillcolor="orange", opacity=0.06, line_width=0)
    fig.add_hrect(y0=0, y1=50, fillcolor="red", opacity=0.05, line_width=0)
    fig.update_layout(height=340, margin=dict(t=10, b=10), yaxis_range=[0, 102])
    st.plotly_chart(fig)

# ---------------------------------------------------------------- Ünite detayı
with tab_unit:
    uid = st.selectbox("Ünite", list(unit_by_id), format_func=lambda k: unit_by_id[k]["name"],
                       index=0)
    u = unit_by_id[uid]
    r = latest.loc[uid]
    icon, color = STATUS_STYLE[r["status"]]

    left, right = st.columns([1, 2])
    with left:
        gauge = go.Figure(go.Indicator(
            mode="gauge+number", value=r["health"], title={"text": "Sağlık skoru"},
            gauge=dict(axis=dict(range=[0, 100]), bar=dict(color=color),
                       steps=[dict(range=[0, 50], color="#fbe3e1"),
                              dict(range=[50, 75], color="#fdf0dc"),
                              dict(range=[75, 100], color="#e3f4ea")])))
        gauge.update_layout(height=230, margin=dict(t=40, b=0, l=35, r=35))
        st.plotly_chart(gauge)
        st.markdown(f"### {icon} {r['status']}")
        if r["pred_fault"] != "normal":
            st.markdown(f"**Olası arıza:** {fault_name(r['pred_fault'])}  \n"
                        f"**Tahmini arızaya kalan süre:** {fmt_eta(r['eta_h'])}")
        st.markdown(f"**Anomali skoru:** {r['anomaly']:.2f}")

    with right:
        probs = pd.DataFrame({
            "Arıza": [fault_name(f) for f in FAULT_TYPES],
            "Olasılık": [r[f"p_{f}"] for f in FAULT_TYPES]}).sort_values("Olasılık")
        fig = px.bar(probs, x="Olasılık", y="Arıza", orientation="h", range_x=[0, 1],
                     text=probs["Olasılık"].map(lambda v: f"%{v * 100:.0f}"))
        fig.update_layout(height=260, margin=dict(t=30, b=10), title="Arıza türü olasılıkları",
                          yaxis_title="")
        st.plotly_chart(fig)

        diag = FAULTS[r["pred_fault"]]
        if r["pred_fault"] != "normal":
            st.warning(f"**Tipik belirtiler:** {diag['belirtiler']}\n\n**Önerilen aksiyon:** {diag['oneri']}")
        else:
            st.success(diag["oneri"])

    reasons = model.explain(r)
    if reasons:
        st.markdown("**Modelin dikkat çektiği sinyaller** (normal çalışmaya göre):")
        st.dataframe(pd.DataFrame(
            [{"Sinyal": n, "Şu an": round(v, 2), "Normal ortalama": round(m, 2),
              "Sapma": f"{'▲' if z > 0 else '▼'} {abs(z):.1f}σ"} for n, v, m, z in reasons]),
            hide_index=True, width="stretch")

    days = st.radio("Grafik aralığı", [1, 3, 7, 14, 30], index=2, horizontal=True,
                    format_func=lambda d: f"Son {d} gün")
    t0 = now - pd.Timedelta(days=days)
    rw = raw[(raw["unit_id"] == uid) & (raw["timestamp"] > t0) & (raw["timestamp"] <= now)]
    hw = cur[(cur["unit_id"] == uid) & (cur["timestamp"] > t0)]
    running = rw["comp_on"] & ~rw["defrost"]
    sig = rw.set_index("timestamp")[["t_room", "t_amb"]].copy()
    for c in ["p_suc", "p_dis", "sh", "sc", "i_comp", "vib", "i_fan"]:
        # Kompresör dururken bu sinyaller anlamsız: yalnızca çalışma anları
        sig[c] = rw[c].where(running).to_numpy()
    freq = {1: None, 3: "15min", 7: "30min", 14: "1h"}.get(days, "2h")
    if freq:
        sig = sig.resample(freq).mean()
    st.caption("Grafikler " + ("5 dakikalık ham veriyi" if freq is None else f"{freq} ortalamalarını")
               + " gösterir; basınç, akım gibi değerler yalnızca kompresör çalışırken alınır.")

    fig = make_subplots(rows=6, cols=1, shared_xaxes=True, vertical_spacing=0.04,
                        subplot_titles=["Sıcaklık (°C)", "Emme / basma basıncı (bar)",
                                        "Kızgınlık / aşırı soğutma (K)", "Kompresör akımı (A)",
                                        "Titreşim (mm/s) / fan akımı (A)", "Sağlık skoru"])
    series = [(1, "t_room", "Oda"), (1, "t_amb", "Dış ortam"), (2, "p_suc", "Emme"),
              (2, "p_dis", "Basma"), (3, "sh", "Kızgınlık"), (3, "sc", "Aşırı soğutma"),
              (4, "i_comp", "Kompresör akımı"), (5, "vib", "Titreşim"), (5, "i_fan", "Fan akımı")]
    for row_i, col, name in series:
        fig.add_scatter(x=sig.index, y=sig[col], name=name, line=dict(width=1.3),
                        connectgaps=freq is not None, row=row_i, col=1)
    fig.add_hline(y=u["setpoint"], line_dash="dot", line_color="gray", row=1, col=1)
    fig.add_scatter(x=hw["timestamp"], y=hw["health"], name="Sağlık", line=dict(width=2, color=color),
                    row=6, col=1)
    fig.update_yaxes(range=[0, 102], row=6, col=1)
    if show_truth and u["fault"] != "normal":
        for h, label in [(u["fault_start_h"], "Arıza başlangıcı"), (u["failure_h"], "Arıza anı")]:
            ts = START + pd.Timedelta(hours=h)
            if t0 < ts <= now:
                fig.add_vline(x=ts, line_dash="dash", line_color="red")
                fig.add_annotation(x=ts, y=1, yref="paper", text=label, showarrow=False,
                                   font=dict(color="red"), xanchor="left")
    fig.update_layout(height=1150, margin=dict(t=30, b=10),
                      legend=dict(orientation="h", yanchor="top", y=-0.03))
    st.plotly_chart(fig)

# ---------------------------------------------------------------- Uyarılar
with tab_alerts:
    events = []
    for uid_, g in cur.groupby("unit_id"):
        prev = g["status"].shift(fill_value="Normal")
        for _, row in g[g["status"] != prev].iterrows():
            events.append({
                "Zaman": fmt_time(row["timestamp"]), "_ts": row["timestamp"],
                "Ünite": unit_by_id[uid_]["name"],
                "Durum": f"{STATUS_STYLE[row['status']][0]} {row['status']}",
                "Olası arıza": fault_name(row["pred_fault"]) if row["pred_fault"] != "normal" else "—",
                "Tahmini süre": fmt_eta(row["eta_h"]),
                "Sağlık": round(row["health"]),
            })
    if events:
        ev = pd.DataFrame(events).sort_values("_ts", ascending=False).drop(columns="_ts")
        st.dataframe(ev, hide_index=True, width="stretch")
        st.caption("Gerçek sistemde bu olaylar SMS / WhatsApp / e-posta ile servis ekibine iletilir.")
    else:
        st.info("Bu zamana kadar uyarı oluşmadı. Kenar çubuğundan zamanı ileri alın.")

# ---------------------------------------------------------------- Performans
with tab_perf:
    st.warning("Bu değerler **sentetik veri** üzerinde ölçülmüştür. Gerçek sahada gürültü, "
               "sensör hataları ve öngörülmeyen durumlar nedeniyle performans daha düşük olacaktır; "
               "gerçek değerler pilot çalışmayla ölçülmelidir.")
    c = st.columns(4)
    c[0].metric("Sınıflandırma doğruluğu", f"%{metrics['accuracy'] * 100:.1f}")
    c[1].metric("Makro F1", f"{metrics['macro_f1']:.3f}")
    if metrics["detection_rate"] is not None:
        c[2].metric("Arızadan önce yakalama", f"%{metrics['detection_rate'] * 100:.0f}")
    if metrics["median_lead_h"] is not None:
        c[3].metric("Medyan erken uyarı", f"{metrics['median_lead_h'] / 24:.1f} gün")
    st.caption(f"Eğitim: {metrics['n_train_units']} ünite × {metrics['days']} gün · "
               f"Test: modelin hiç görmediği {metrics['n_test_units']} ünite · "
               f"Yanlış alarm veren ünite: {metrics['false_alarm_units']}")

    left, right = st.columns(2)
    with left:
        names = [short_name(f) for f in metrics["labels"]]
        cm = np.array(metrics["confusion_matrix"])
        cm_pct = cm / cm.sum(axis=1, keepdims=True).clip(min=1)
        fig = px.imshow(cm_pct, x=names, y=names, text_auto=".0%", color_continuous_scale="Blues",
                        labels=dict(x="Tahmin", y="Gerçek", color="Oran"))
        fig.update_layout(height=450, title="Karışıklık matrisi (saatlik)", coloraxis_showscale=False)
        st.plotly_chart(fig)
    with right:
        rows = [{"Arıza": fault_name(f), "Kesinlik": round(v["precision"], 3),
                 "Duyarlılık": round(v["recall"], 3), "F1": round(v["f1-score"], 3),
                 "Örnek (saat)": int(v["support"])} for f, v in metrics["per_class"].items()]
        st.markdown("**Sınıf bazında sonuçlar**")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        ew = pd.DataFrame([e for e in metrics["early_warning"]
                           if e["fault"] != "normal" and e.get("lead_h") is not None])
        if not ew.empty:
            ew["Arıza"] = ew["fault"].map(short_name)
            fig = px.box(ew, x="Arıza", y=ew["lead_h"] / 24, points="all",
                         labels={"y": "Erken uyarı süresi (gün)", "Arıza": ""})
            fig.update_layout(height=300, title="Arızadan kaç gün önce doğru uyarı verildi?")
            st.plotly_chart(fig)

# ---------------------------------------------------------------- Hakkında
with tab_about:
    st.markdown("""
### Nasıl çalışır?

**1. Veri toplama** — Her soğuk odadan 5 dakikada bir: emme/basma basıncı, oda, dış ortam,
evaporatör bataryası ve basma hattı sıcaklıkları, kompresör ve fan akımı, titreşim,
kompresör/defrost/kapı durumu. *(Bu demoda veriler fizik esaslı bir simülatörle üretilmektedir.)*

**2. Öznitelik çıkarımı** — Ham veriden saatlik olarak türetilir: kızgınlık, aşırı soğutma,
yoğuşma yaklaşımı (yoğuşma − dış ortam), oda−evaporatör farkı, kompresör çalışma oranı,
kalkış sayısı, defrost tepe sıcaklığı vb. (12 saatlik kayan pencere).

**3. Yapay zekâ modelleri**
- **Anomali tespiti (Isolation Forest):** Yalnızca sağlıklı veriden öğrenir. Arıza etiketi
  gerektirmediği için gerçek sahada ilk günden kullanılabilir.
- **Arıza türü sınıflandırıcısı (Gradient Boosting):** 5 arıza türünü ayırt eder.
- **Kalan süre tahmini (Gradient Boosting regresyon):** Arızaya kaç saat kaldığını tahmin eder.

**4. Sağlık skoru** — Sınıflandırıcı ve anomali skorunun birleşimi (0–100):
🟢 ≥75 Normal · 🟠 50–75 İzlemede · 🔴 <50 Kritik.

### Gerçek sahaya geçiş planı
1. Pilot sahalara sensör ve IoT ağ geçidi kurulumu (veya mevcut kontrol cihazlarından Modbus ile okuma)
2. 1–3 ay veri toplama; servis kayıtlarının arızalarla eşleştirilmesi
3. Simülatörde eğitilen modelin gerçek veriyle ince ayarı (transfer öğrenme)
4. Panelin bulut ortamına taşınması, SMS/WhatsApp bildirimleri
""")

# ---------------------------------------------------------------- Oynatma
if play:
    if t_now < max_h:
        time.sleep({"Yavaş": 1.5, "Normal": 0.8, "Hızlı": 0.3}[speed])
        st.session_state["_next_t"] = min(t_now + 3, max_h)
        st.rerun()
