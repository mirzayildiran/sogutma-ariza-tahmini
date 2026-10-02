"""Maliyet ve Kazanç: kullanıcının kendi rakamlarıyla basit bir ROI hesaplayıcısı."""

from dataclasses import replace

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from sogutma.roi import RoiGirdi, duyarlilik, hesapla
from sogutma.ui import fmt_tl, load_metrics, root

ORNEK = "örnek değer — kendi rakamlarınızı girin"

metrics = load_metrics(str(root()))
sentetik_oran = metrics.get("detection_rate") if metrics else None
varsayilan_oran = int(round(sentetik_oran * 100)) if sentetik_oran is not None else 50

st.title("💰 Maliyet ve Kazanç Hesaplayıcı")
st.warning(
    "**Bu sayfadaki tüm başlangıç değerleri örnek yer tutucudur; piyasa verisi ya da vaat değildir.** "
    "Sonuçlar yalnızca sizin gireceğiniz rakamlar kadar anlamlıdır. Kendi arıza geçmişinizi, onarım "
    "faturalarınızı ve ürün kayıplarınızı girin. Hesap basit bir çerçevedir; vergi, enflasyon ve "
    "iskonto hesaba katılmaz.")

sol, sag = st.columns([2, 3], gap="large")

with sol:
    st.subheader("Girdiler")
    st.caption(f"Başlangıç değerleri: {ORNEK}.")
    with st.container(border=True):
        st.markdown("**Filo ve arıza geçmişi**")
        unite = st.number_input("Ünite sayısı", key="roi_unite", min_value=1, value=10, step=1,
                                help=f"İzlenecek soğuk oda / ünite sayısı ({ORNEK}).")
        sikilik = st.number_input(
            "Ünite başına yılda plansız arıza sayısı", key="roi_sikilik", min_value=0.0, value=0.5,
            step=0.1, format="%.2f", help=f"Kendi servis kayıtlarınızdan hesaplayın ({ORNEK}).")
        acil = st.number_input(
            "Ortalama acil onarım maliyeti (TL / arıza)", key="roi_acil", min_value=0, value=15000,
            step=500, help=f"Acil servis, parça, iş gücü ({ORNEK}).")
        urun = st.number_input(
            "Ortalama ürün kaybı (TL / arıza)", key="roi_urun", min_value=0, value=40000,
            step=1000, help=f"Bozulan ürün, iade, satış kaybı ({ORNEK}).")
    with st.container(border=True):
        st.markdown("**Erken uyarının etkisi**")
        oran = st.slider("Erken yakalanan arıza oranı (%)", 0, 100, varsayilan_oran, key="roi_oran",
                         help="Arızaların kaçı, ürün zarar görmeden ya da acil servis gerekmeden önce "
                              "uyarıyla yakalanır?")
        if sentetik_oran is not None:
            st.caption(
                f"Başlangıç değeri, sentetik test filosunda ölçülen yakalama oranıdır "
                f"(%{sentetik_oran * 100:.0f}). **Gerçek sahada bu oran daha düşük olacaktır**; gerçek değer "
                "ancak pilot çalışmayla ölçülebilir. Temkinli bir senaryo için değeri düşürün.")
        else:
            st.caption(f"Başlangıç değeri: {ORNEK}. Gerçek değer ancak pilot çalışmayla ölçülebilir.")
        onarim_tas = st.slider(
            "Erken yakalanınca onarım maliyetinde tasarruf (%)", 0, 100, 40, key="roi_onarim",
            help="Planlı onarım ile acil onarım arasındaki maliyet farkının acil onarıma "
                 f"oranı ({ORNEK}).")
        urun_koruma = st.slider("Erken yakalanınca önlenen ürün kaybı (%)", 0, 100, 80, key="roi_koruma",
                                help=f"Önceden uyarıldığında ürünün ne kadarı kurtarılır ({ORNEK}).")
    with st.container(border=True):
        st.markdown("**Sistem maliyeti**")
        aylik = st.number_input(
            "Ünite başına aylık sistem maliyeti (TL)", key="roi_aylik", min_value=0, value=300,
            step=50, help=f"Abonelik, bağlantı, bakım ({ORNEK}).")
        kurulum = st.number_input(
            "Ünite başına tek seferlik kurulum maliyeti (TL)", key="roi_kurulum", min_value=0,
            value=6000, step=500, help=f"Sensör, ağ geçidi, işçilik ({ORNEK}).")

g = RoiGirdi(
    unite_sayisi=unite, ariza_per_unite_yil=sikilik, acil_onarim_tl=acil, urun_kaybi_tl=urun,
    erken_yakalama_orani=oran / 100, onarim_tasarruf_orani=onarim_tas / 100,
    urun_koruma_orani=urun_koruma / 100,
    sistem_aylik_tl_unite=aylik, kurulum_tl_unite=kurulum)
s = hesapla(g)

with sag:
    st.subheader("Sonuç")
    c = st.columns(2)
    c[0].metric("Yıllık önlenen maliyet", fmt_tl(s.yillik_onlenen_tl),
                help=f"{s.yillik_yakalanan:.1f} erken yakalanan arıza × "
                     f"arıza başına {fmt_tl(s.ariza_basi_kazanc_tl)}")
    c[1].metric("Yıllık sistem maliyeti", fmt_tl(s.yillik_sistem_tl))
    c = st.columns(2)
    c[0].metric("Yıllık net fayda", fmt_tl(s.yillik_net_tl),
                delta="kazanç" if s.yillik_net_tl > 0 else "zarar" if s.yillik_net_tl < 0 else None,
                delta_color="normal" if s.yillik_net_tl >= 0 else "inverse")
    if s.geri_odeme_ay is None:
        c[1].metric("Geri ödeme süresi", "Geri ödenmiyor")
    elif s.geri_odeme_ay < 1:
        c[1].metric("Geri ödeme süresi", "< 1 ay")
    else:
        c[1].metric("Geri ödeme süresi", f"{s.geri_odeme_ay:.1f} ay",
                    help=f"Tek seferlik kurulum maliyeti ({fmt_tl(s.kurulum_tl)}) / aylık net fayda")
    if s.geri_odeme_ay is None:
        st.error("Bu girdilerle yıllık net fayda sıfır ya da negatif: sistem maliyetini karşılamıyor.")
    st.caption(f"Beklenen yıllık plansız arıza: {s.yillik_ariza:.1f} · "
               f"erken yakalanan: {s.yillik_yakalanan:.1f} · tek seferlik kurulum: {fmt_tl(s.kurulum_tl)}")

    # 3 yıllık kümülatif net
    aylar = list(range(len(s.kumulatif_tl)))
    fig = go.Figure(go.Scatter(x=aylar, y=s.kumulatif_tl, mode="lines", fill="tozeroy",
                               line=dict(width=2.5, color="#2e9e5b" if s.yillik_net_tl >= 0 else "#d0342c"),
                               hovertemplate="%{x}. ay: %{y:,.0f} TL<extra></extra>"))
    fig.add_hline(y=0, line_dash="dot", line_color="gray")
    if s.geri_odeme_ay is not None and s.geri_odeme_ay <= aylar[-1]:
        fig.add_vline(x=s.geri_odeme_ay, line_dash="dash", line_color="gray",
                      annotation_text="geri ödeme", annotation_position="top left")
    fig.update_layout(title="3 yıllık kümülatif net kazanç (kurulum dahil, TL)", height=330,
                      margin=dict(t=50, b=10), xaxis_title="Ay", yaxis_title="TL", xaxis_range=[0, aylar[-1]])
    st.plotly_chart(fig)

# ---------------------------------------------------------------- Duyarlılık
st.subheader("Duyarlılık: erken yakalama oranı")
st.caption("Diğer girdiler sabit tutulurken, erken yakalama oranı değişirse yıllık net fayda nasıl değişir? "
           "Gerçek yakalama oranı belirsiz olduğu için bu görünüm, sonucun buna ne kadar bağlı olduğunu "
           "gösterir.")
oranlar = np.linspace(0, 1, 21)
net = duyarlilik(g, oranlar)
fig = go.Figure(go.Scatter(x=oranlar * 100, y=net, mode="lines", line=dict(width=2.5, color="#3b6fb6"),
                           hovertemplate="Oran %{x:.0f}: %{y:,.0f} TL<extra></extra>",
                           name="Yıllık net fayda"))
fig.add_hline(y=0, line_dash="dot", line_color="gray")
fig.add_trace(go.Scatter(x=[oran], y=[s.yillik_net_tl], mode="markers", name="Seçili oran",
                         marker=dict(size=11, color="#d0342c")))
if s.basabas_yakalama_orani is not None and s.basabas_yakalama_orani <= 1:
    fig.add_vline(x=s.basabas_yakalama_orani * 100, line_dash="dash", line_color="gray",
                  annotation_text="başabaş", annotation_position="top left")
fig.update_layout(height=340, margin=dict(t=20, b=10), xaxis_title="Erken yakalanan arıza oranı (%)",
                  yaxis_title="Yıllık net fayda (TL)", legend=dict(orientation="h", y=-0.25))

sol2, sag2 = st.columns([3, 2], gap="large")
with sol2:
    st.plotly_chart(fig)
with sag2:
    if s.basabas_yakalama_orani is None:
        st.info("Arıza başına önlenen maliyet sıfır olduğu için başabaş oranı hesaplanamıyor.")
    elif s.basabas_yakalama_orani > 1:
        st.warning("Yakalama oranı %100 olsa bile bu girdilerle sistem maliyeti karşılanmıyor.")
    else:
        st.info(f"**Başabaş yakalama oranı: %{s.basabas_yakalama_orani * 100:.0f}.** Bu oranın altında "
                "yıllık sistem maliyeti, önlenen maliyetten büyüktür.")
    rows = []
    for r in (0.25, 0.5, 0.75, 1.0):
        sr = hesapla(replace(g, erken_yakalama_orani=r))
        rows.append({"Yakalama oranı": f"%{r * 100:.0f}", "Yıllık net fayda": fmt_tl(sr.yillik_net_tl),
                     "Geri ödeme": "—" if sr.geri_odeme_ay is None else f"{sr.geri_odeme_ay:.1f} ay"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

with st.expander("Hesap nasıl yapılıyor?"):
    st.markdown("""
- **Yıllık arıza** = ünite sayısı × ünite başına yıllık arıza
- **Arıza başına önlenen maliyet** = acil onarım × onarım tasarrufu % + ürün kaybı × önlenen kayıp %
- **Yıllık önlenen maliyet** = yıllık arıza × erken yakalama oranı × arıza başına önlenen maliyet
- **Yıllık sistem maliyeti** = ünite sayısı × aylık maliyet × 12
- **Yıllık net fayda** = yıllık önlenen maliyet − yıllık sistem maliyeti
- **Geri ödeme (ay)** = tek seferlik kurulum / (yıllık net fayda / 12); net fayda ≤ 0 ise geri ödeme yoktur
- **Kümülatif net** = −kurulum + aylık net fayda × ay

Yakalanmayan arızalar için sistemin bir etkisi olmadığı varsayılır. Yanlış alarmların maliyeti
(gereksiz servis çağrısı) bu hesaba **dahil değildir**; pilot çalışmada ölçülmelidir.
""")
