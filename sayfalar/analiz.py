"""Kendi Verini Analiz Et: kullanıcının sensör CSV'sini modelle değerlendirir (predict.py ile aynı akış)."""

import io
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from sogutma.analiz import YetersizVeri, analiz_et, son_durum
from sogutma.faults import FAULTS, fault_name, short_name
from sogutma.features import FEATURE_LABELS
from sogutma.ingest import SCHEMA, ValidationError
from sogutma.ui import STATUS_STYLE, fmt_eta, load_model, root, saglik_grafigi, sinyal_grafigi, sinyal_notu
from sogutma.veri_kalitesi import NEDEN_ADI, NOTRLEYEN, SENSOR_ADI

TIP_SECENEKLERI = {None: "CSV'deki tip sütunu / soğuk oda", "soguk_oda": "Soğuk oda",
                   "dondurucu": "Dondurucu", "market_dolabi": "Market dolabı"}
ORNEK = Path(__file__).resolve().parent.parent / "examples" / "ornek_veri.csv"
DOKUMAN = "https://github.com/mirzayildiran/sogutma-ariza-tahmini/blob/main/docs/veri-formati.md"


@st.cache_data(show_spinner="Veri analiz ediliyor...", max_entries=4)
def calistir(veri: bytes, gauge: bool, setpoint, tip, root_dir: str):
    """(analiz, hatalar) döndürür; hatalar Türkçe mesaj listesidir. Sonuç önbelleğe alınır."""
    try:
        sonuc = analiz_et(io.BytesIO(veri), load_model(root_dir), gauge=gauge, setpoint=setpoint, tip=tip)
        return sonuc, None
    except ValidationError as e:
        return None, e.hatalar
    except YetersizVeri as e:
        return None, [str(e)]


st.title("📥 Kendi Verini Analiz Et")
st.warning(
    "**Sınırlama:** Model **R404A** soğutucu akışkan ve soğuk oda, dondurucu ve market dolabı için "
    "**sentetik (simülatör) veriyle** eğitildi. Başka gaz, farklı ekipman (ör. chiller) ya da gerçek saha "
    "gürültüsü için sonuçlar **doğrulanmamıştır**; çıktıyı karar vermek için değil, **ön gösterge** "
    "olarak kullanın. Ekipman tipinin doğru seçilmesi önemlidir.")

# ---------------------------------------------------------------- Girdi
sol, sag = st.columns([3, 2])
with sol:
    yuklenen = st.file_uploader("Sensör verisi (CSV)", type=["csv", "txt"],
                                help="Ayraç, ondalık işareti ve kodlama otomatik algılanır; "
                                     "Türkçe Excel çıktısı desteklenir.")
    ornek_tikla = st.button(
        "Örnek veriyi kullan", icon="🧪",
        help="examples/ornek_veri.csv: 10 günlük, 5. günden itibaren gaz kaçağı gelişen bir oda.")
with sag:
    with st.container(border=True):
        st.markdown("**İsteğe bağlı ayarlar**")
        set_gir = st.checkbox("Set değerini kendim gireyim",
                              help="CSV'de set değeri sütunu varsa o kullanılır; sütun yoksa bu değer, "
                                   "o da yoksa oda sıcaklığı medyanı kullanılır.")
        setpoint = st.number_input("Termostat set değeri (°C)", value=2.0, step=0.5, disabled=not set_gir)
        tip_secim = st.selectbox(
            "Ekipman tipi", list(TIP_SECENEKLERI), format_func=TIP_SECENEKLERI.get,
            help="CSV'de `tip` sütunu varsa o kullanılır. Yanlış tip (ör. dondurucuyu soğuk oda olarak "
                 "vermek) sağlıklı üniteyi arızalı gösterebilir.")
        gauge = st.checkbox("Basınçlar efektif (gauge) ölçülmüş",
                            help="İşaretlerseniz basınçlara 1,013 bar eklenerek mutlak basınca çevrilir. "
                                 "Model mutlak basınç (bar) bekler.")

# Kaynak oturumda tutulur: ayar değişince ya da başka sayfadan dönünce analiz kaybolmaz
if ornek_tikla:
    st.session_state["analiz_kaynak"] = (ORNEK.name, ORNEK.read_bytes())
elif yuklenen is not None and st.session_state.get("analiz_dosya_id") != yuklenen.file_id:
    st.session_state["analiz_dosya_id"] = yuklenen.file_id  # yeni yüklenen dosya örneğin önüne geçer
    st.session_state["analiz_kaynak"] = (yuklenen.name, yuklenen.getvalue())

if "analiz_kaynak" in st.session_state:
    ad, veri = st.session_state["analiz_kaynak"]
else:
    st.info("Bir CSV dosyası yükleyin ya da **Örnek veriyi kullan** düğmesine basın. "
            "Dosya sunucuya kaydedilmez; yalnızca bu oturumda analiz edilir.")
    with st.expander("Beklenen sütunlar"):
        st.markdown(
            "Başlıklar büyük/küçük harfe ve Türkçe karaktere duyarsızdır; birim eki "
            "(`Oda Sıcaklığı (°C)`) yok sayılır. Ayrıntılar: "
            f"[Veri formatı belgesi]({DOKUMAN}).")
        st.dataframe(pd.DataFrame([
            {"Sütun": k, "Zorunlu": "Evet" if s["zorunlu"] else "Hayır", "Birim": s["birim"],
             "Açıklama": s["aciklama"]} for k, s in SCHEMA.items()]), hide_index=True, width="stretch")
    st.stop()

analiz, hatalar = calistir(veri, gauge, setpoint if set_gir else None, tip_secim, str(root()))
if hatalar:
    st.error("**Veri dosyası kullanılamıyor.** Aşağıdaki sorunları giderip yeniden deneyin:\n\n"
             + "\n".join(f"- {h}" for h in hatalar))
    st.caption(f"Sütun adları, birimler ve sınırlar için: [Veri formatı belgesi]({DOKUMAN}).")
    st.stop()

raw, H, pred, rapor = analiz.raw, analiz.H, analiz.pred, analiz.rapor
model = load_model(str(root()))

n_satir = f"{len(raw):,}".replace(",", ".")
st.success(f"**{ad}** analiz edildi: {n_satir} örnek (5 dk), {len(analiz.uniteler)} ünite, "
           f"{raw['timestamp'].min():%d.%m.%Y} – {raw['timestamp'].max():%d.%m.%Y}.")

# ---------------------------------------------------------------- Veri kontrol raporu
with st.expander("Veri kontrol raporu", expanded=bool(rapor.uyarilar or analiz.belirsiz)):
    for u in rapor.uyarilar:
        st.warning(u)
    for b in rapor.bilgiler:
        st.info(b)
    if analiz.belirsiz:
        st.info(f"{len(analiz.belirsiz)} öznitelik hiç hesaplanamadı (ilgili sensör eksik) ve nötr kabul "
                "edildi: " + ", ".join(FEATURE_LABELS[f] for f in analiz.belirsiz) + ". Bu durum arıza "
                "türü tahmininin güvenilirliğini düşürebilir.")
    if rapor.turetilen:
        st.markdown("**Türetilen / varsayılan alanlar**")
        st.dataframe(pd.DataFrame(
            [{"Alan": k, "Nasıl elde edildi": v.replace("--setpoint değeri", "girilen set değeri")}
             for k, v in rapor.turetilen.items()]), hide_index=True, width="stretch")
    if rapor.uniteler:
        st.markdown("**Ünite kapsamı**")
        st.dataframe(pd.DataFrame([
            {"Ünite": uid, "Başlangıç": f"{b['bas']:%d.%m.%Y %H:%M}", "Bitiş": f"{b['bit']:%d.%m.%Y %H:%M}",
             "Geçerli veri (saat)": round(b["saat"], 1), "Kapsam": f"%{b['kapsam'] * 100:.0f}",
             "Örnekleme (dk)": round(b["orneklem_dk"], 1)} for uid, b in rapor.uniteler.items()]),
            hide_index=True, width="stretch")
    if not (rapor.uyarilar or rapor.bilgiler or analiz.belirsiz):
        st.success("Uyarı yok: tüm zorunlu ve isteğe bağlı sensörler okundu.")

# ---------------------------------------------------------------- Ünite özeti
durumlar = {}
for uid in analiz.uniteler:
    h_u, p_u = analiz.unite(uid)
    durumlar[uid] = son_durum(p_u, h_u, model)
st.subheader("Ünite durumu (son saat)")
st.dataframe(pd.DataFrame([{
    "Ünite": uid,
    "Durum": f"{STATUS_STYLE[d['status']][0]} {d['status']}",
    "Sağlık": round(d["health"]),
    "Tahmini arıza": fault_name(d["pred_fault"]) if d["pred_fault"] != "normal" else "—",
    "Güven": f"%{d['confidence'] * 100:.0f}",
    "Tahmini kalan süre": fmt_eta(d["eta_h"]),
    "Veri kalitesi": "⚠️ Sensör şüphesi" if d["sensor_sorunu"] else "—",
    "Uyarı süresi": f"{d['uyari_saat']} saat" if d["uyari_saat"] else "—",
    "Son veri": f"{d['zaman']:%d.%m.%Y %H:%M}",
} for uid, d in durumlar.items()]), hide_index=True, width="stretch")

uid = st.selectbox("Ayrıntısını görmek istediğiniz ünite", analiz.uniteler) if len(analiz.uniteler) > 1 \
    else analiz.uniteler[0]
d = durumlar[uid]
icon, color = STATUS_STYLE[d["status"]]

c = st.columns(4)
c[0].metric("Sağlık skoru", f"{d['health']:.0f} / 100")
c[1].metric("Durum", f"{icon} {d['status']}")
c[2].metric("Olası arıza", short_name(d["pred_fault"]) if d["pred_fault"] != "normal" else "Yok",
            help="Model arıza türü olasılıklarından en yükseği; yalnızca durum Normal değilse gösterilir.")
c[3].metric("Tahmini kalan süre", fmt_eta(d["eta_h"]),
            help="Kaba tahmin; sentetik veriyle eğitilmiş regresyon.")

if d["sensor_sorunu"]:
    st.warning(f"**{d['sensor_notu']}.** Önce sensörü ve kablolamayı kontrol edin: ilgili öznitelikler "
               "arıza değerlendirmesinden çıkarıldı, yani aşağıdaki sonuç bu sensörün sinyalini içermez ve "
               "bir ekipman arızası olarak yorumlanmamalıdır.")

if d["pred_fault"] != "normal":
    st.warning(f"**Tipik belirtiler:** {FAULTS[d['pred_fault']]['belirtiler']}\n\n"
               f"**Önerilen aksiyon:** {d['oneri']}")
    if d["uyari_saat"]:
        st.caption(f"{d['uyari_baslangic']:%d.%m.%Y %H:%M} tarihinden beri "
                   f"Normal dışı ({d['uyari_saat']} saat).")
else:
    st.success("Belirgin bir arıza belirtisi yok. " + d["oneri"])

if d["sapmalar"]:
    st.markdown("**Normalden en çok sapan sinyaller** (modelin sağlıklı eğitim verisine göre):")
    st.dataframe(pd.DataFrame(
        [{"Sinyal": n, "Şu an": round(v, 2), "Normal ortalama": round(m, 2),
          "Sapma": f"{'▲' if z > 0 else '▼'} {abs(z):.1f}σ"} for n, v, m, z in d["sapmalar"]]),
        hide_index=True, width="stretch")

# ---------------------------------------------------------------- Sensör sağlığı
st.subheader(f"Sensör sağlığı · {uid}")
sorunlar = analiz.sensor_sorunlari(uid)
if sorunlar.empty:
    st.success("Sensör sağlığı kontrolünde sorun bulunmadı (takılı, kopuk, aralık dışı, ani sıçrama, "
               "veri kaybı, gürültü ve sensörler arası tutarlılık denetlendi).")
else:
    st.caption("Takılı, kopuk, veri kaybı, tutarsız (kayma şüphesi) ve gürültülü sensörlerin öznitelikleri "
               "model girdisinden çıkarılır; ani sıçrama ve aralık dışı örnekler silinir. Kayma yalnızca "
               "başka sensörlerle fiziksel ilişkisi olan sinyallerde (basınçlar, sıcaklıklar, akım/titreşim "
               "sıfır referansı) yakalanır; eşikler sentetik veriye göre ayarlıdır.")
    st.dataframe(pd.DataFrame([{
        "Sensör": SENSOR_ADI[r.sensor], "Sorun": NEDEN_ADI[r.neden],
        "Modelden çıkarıldı": "Evet" if r.neden in NOTRLEYEN else "Hayır (örnekler temizlendi)",
        "İlk görülme": f"{r.ilk:%d.%m.%Y %H:%M}", "Son görülme": f"{r.son:%d.%m.%Y %H:%M}",
        "Etkilenen saat": int(r.saat)} for r in sorunlar.itertuples()]), hide_index=True, width="stretch")
    kodlar = analiz.kalite.saatlik.loc[uid]
    kodlar = kodlar.loc[:, (kodlar != "").any()]
    z = kodlar.apply(lambda c: c.map(lambda k: 2 if k in NOTRLEYEN else (1 if k else 0))).T
    metin = kodlar.apply(lambda c: c.map(lambda k: NEDEN_ADI.get(k, "sorun yok"))).T
    isi = go.Figure(go.Heatmap(
        z=z.to_numpy(), x=z.columns, y=[SENSOR_ADI[s] for s in z.index], showscale=False,
        text=metin.to_numpy(), hovertemplate="%{y}<br>%{x}<br>%{text}<extra></extra>",
        colorscale=[[0, "#d9f0d3"], [0.33, "#d9f0d3"], [0.34, "#fdae6b"], [0.66, "#fdae6b"],
                    [0.67, "#d7301f"], [1, "#d7301f"]], zmin=0, zmax=2))
    isi.update_layout(height=60 + 38 * len(z), margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(isi)
    st.caption("Yeşil: sorun yok · turuncu: örnekler temizlendi · kırmızı: sensör modelden çıkarıldı.")

# ---------------------------------------------------------------- Grafikler
rapor_df = analiz.rapor_tablosu()
st.subheader("Sağlık skoru geçmişi")
st.plotly_chart(saglik_grafigi(rapor_df))

st.subheader(f"Sensör grafikleri · {uid}")
ru = raw[raw["unit_id"] == uid]
span = max(1, int(round((ru["timestamp"].max() - ru["timestamp"].min()) / pd.Timedelta(days=1))))
secenekler = [g for g in (1, 3, 7, 14, 30) if g < span] + [0]
gun = st.radio("Grafik aralığı", secenekler, index=len(secenekler) - 1, horizontal=True,
               format_func=lambda g: "Tümü" if g == 0 else f"Son {g} gün", key=f"analiz_aralik_{uid}")
gun_say = span if gun == 0 else gun
bit = ru["timestamp"].max()
t0 = bit - pd.Timedelta(days=gun_say)
rw = ru[ru["timestamp"] > t0]
hw = rapor_df[(rapor_df["unit_id"] == uid) & (rapor_df["timestamp"] > t0)]
set_deger = float(ru["setpoint"].median())
fig, freq = sinyal_grafigi(rw, hw, set_deger, color, gun_say)
st.caption(sinyal_notu(freq) + " Eksik sensörlerin grafikleri çizilmez.")
st.plotly_chart(fig)

# ---------------------------------------------------------------- İndirme
st.subheader("Saatlik rapor")
st.dataframe(rapor_df.tail(48), hide_index=True, width="stretch")
st.caption("Son 48 saat gösteriliyor; indirilen dosya tüm saatleri içerir "
           "(`predict.py` çıktısıyla aynı sütunlar).")
st.download_button("Saatlik raporu indir (CSV)", rapor_df.to_csv(index=False).encode("utf-8"),
                   file_name=f"{Path(ad).stem}_rapor.csv", mime="text/csv", icon="⬇️")
