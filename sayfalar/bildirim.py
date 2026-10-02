"""Bildirim Ayarları: uyarı kuralları ve kanalları için ayar formu, doğrulama ve deneme modu önizlemesi.

Bu sayfadan gerçek gönderim yapılmaz ve parola/token alınmaz: gizli bilgiler yalnızca sunucunun
ortam değişkenlerinden okunur (burada yalnızca tanımlı olup olmadıkları gösterilir).
Kurallar `sogutma/bildirim.py` ile aynı şemayı kullanır; doğrulamayı da o modülün yükleyicisi yapar.
"""

import json
import os
from dataclasses import replace
from datetime import time

import pandas as pd
import streamlit as st

from sogutma.bildirim import (
    ENV_SMTP_KULLANICI,
    ENV_SMTP_PAROLA,
    ENV_TELEGRAM_TOKEN,
    ENV_WEBHOOK_SIRRI,
    ENV_WEBHOOK_URL,
    AyarHatasi,
    Gonderici,
    Kurallar,
    isle,
)
from sogutma.faults import fault_name
from sogutma.simulator import START
from sogutma.ui import STATUS_STYLE, fmt_time, load_demo, root

DOKUMAN = "https://github.com/mirzayildiran/sogutma-ariza-tahmini/blob/main/docs/bildirimler.md"

# Ayar formunun oturum anahtarları ve başlangıç değerleri (sogutma.bildirim.Kurallar varsayılanlarıyla uyumlu)
VARSAYILAN = {
    "bd_durumlar": ["İzlemede", "Kritik"],
    "bd_iyilesme": True,
    "bd_hatirlatma": False,
    "bd_min_guven": 50,
    "bd_cooldown": 12.0,
    "bd_surekli_izlemede": 6,
    "bd_surekli_kritik": 3,
    "bd_iyilesme_saat": 6,
    "bd_eski_sinirli": True,
    "bd_eski_saat": 48.0,
    "bd_sessiz": False,
    "bd_sessiz_bas": time(22, 0),
    "bd_sessiz_bit": time(7, 0),
    "bd_sessiz_kritik": True,
    "bd_konsol": True,
    "bd_eposta": False,
    "bd_smtp_sunucu": "",
    "bd_smtp_port": 587,
    "bd_smtp_guv": "starttls",
    "bd_smtp_kullanici": "",
    "bd_smtp_gonderen": "",
    "bd_smtp_alicilar": "",
    "bd_telegram": False,
    "bd_tg_idler": "",
    "bd_webhook": False,
    "bd_wh_url": "",
    "bd_birim_adlari": {},
}
# Kanal başına bilinen alanlar; ayar dosyasında bunların dışındaki alanlar (ör. parola) alınmaz
KANAL_ALANLARI = {
    "konsol": {"aktif"},
    "telegram": {"aktif", "sohbet_idleri"},
    "webhook": {"aktif", "url"},
    "eposta": {"aktif", "sunucu", "port", "guvenlik", "kullanici", "gonderen", "alicilar"},
}
ORTAM = [  # (değişken, kanal)
    (ENV_SMTP_PAROLA, "E-posta"),
    (ENV_SMTP_KULLANICI, "E-posta"),
    (ENV_TELEGRAM_TOKEN, "Telegram"),
    (ENV_WEBHOOK_URL, "Webhook"),
    (ENV_WEBHOOK_SIRRI, "Webhook"),
]
TUR_ADLARI = {
    "uyari": "Uyarı",
    "kritik": "Kritik uyarı",
    "yukselis": "Yükselme",
    "iyilesme": "İyileşme",
    "hatirlatma": "Hatırlatma",
    "ariza_degisti": "Arıza türü değişti",
}
KANAL_ADLARI = {"konsol": "Konsol", "eposta": "E-posta", "telegram": "Telegram", "webhook": "Webhook"}


def sade(v):
    """12.0 → 12 (JSON'da gereksiz ondalık olmasın)."""
    return int(v) if float(v).is_integer() else v


def saat_oku(s, varsayilan):
    try:
        h, m = str(s).split(":")
        return time(int(h), int(m))
    except ValueError:
        return varsayilan


def satirlar(metin):
    """Satır sonu / virgül / noktalı virgülle ayrılmış girdiyi temiz bir listeye çevirir."""
    return [p.strip() for p in metin.replace(";", "\n").replace(",", "\n").splitlines() if p.strip()]


def ayar_uygula(d):
    """Yüklenen ayar sözlüğünü forma işler. Geçersizse AyarHatasi; döndürür: uyarı metinleri."""
    k = Kurallar.from_dict(d)  # kendi doğrulamasını yapar
    s = st.session_state
    uyarilar = []
    if d.get("deneme_modu") is False:
        uyarilar.append(
            "Dosyadaki `deneme_modu: false` alınmadı; bu sayfa her zaman `true` yazar. "
            "Gerçek gönderim yalnızca `predict.py --gonder` ile yapılır."
        )
    s["bd_durumlar"] = list(k.bildirilen_durumlar)
    s["bd_iyilesme"], s["bd_hatirlatma"] = k.iyilesme_bildir, k.hatirlatma
    s["bd_min_guven"] = int(round(k.min_guven * 100))
    s["bd_cooldown"] = float(k.cooldown_saat)
    s["bd_surekli_izlemede"], s["bd_surekli_kritik"] = k.surekli_saat["İzlemede"], k.surekli_saat["Kritik"]
    s["bd_iyilesme_saat"] = k.iyilesme_saat
    s["bd_eski_sinirli"] = k.en_eski_olay_saat is not None
    s["bd_eski_saat"] = float(k.en_eski_olay_saat if k.en_eski_olay_saat is not None else 48)
    ss = k.sessiz_saatler
    s["bd_sessiz"], s["bd_sessiz_kritik"] = ss.aktif, ss.kritik_haric
    s["bd_sessiz_bas"], s["bd_sessiz_bit"] = (
        saat_oku(ss.baslangic, time(22, 0)),
        saat_oku(ss.bitis, time(7, 0)),
    )
    s["bd_birim_adlari"] = dict(k.birim_adlari)

    ch = k.kanallar
    for ad, alanlar in KANAL_ALANLARI.items():
        c = ch.get(ad, {})
        yok = sorted(set(c) - alanlar)
        if yok:
            uyarilar.append(
                f"kanallar.{ad}: {', '.join(yok)} alanı yok sayıldı "
                "(parola, token ve sır ayar dosyasında tutulmaz; ortam değişkeniyle verilir)."
            )
    s["bd_konsol"] = bool(ch.get("konsol", {}).get("aktif", False))
    e, t, w = ch.get("eposta", {}), ch.get("telegram", {}), ch.get("webhook", {})
    s["bd_eposta"] = bool(e.get("aktif", False))
    s["bd_smtp_sunucu"], s["bd_smtp_gonderen"] = str(e.get("sunucu", "")), str(e.get("gonderen", ""))
    s["bd_smtp_kullanici"] = str(e.get("kullanici", ""))
    s["bd_smtp_guv"] = "ssl" if e.get("guvenlik") == "ssl" else "starttls"
    try:
        s["bd_smtp_port"] = int(e.get("port", 465 if s["bd_smtp_guv"] == "ssl" else 587))
    except (TypeError, ValueError):
        s["bd_smtp_port"] = 587
    s["bd_smtp_alicilar"] = "\n".join(str(a) for a in e.get("alicilar", []))
    s["bd_telegram"], s["bd_tg_idler"] = (
        bool(t.get("aktif", False)),
        "\n".join(str(i) for i in t.get("sohbet_idleri", [])),
    )
    s["bd_webhook"], s["bd_wh_url"] = bool(w.get("aktif", False)), str(w.get("url", ""))
    return uyarilar


def ayar_olustur():
    """Formdaki değerlerden bildirim ayarı sözlüğü (bildirim.py şeması) üretir."""
    s = st.session_state
    d = {
        "_aciklama": "Bildirim ayarı (panelde oluşturuldu). Parola, token ve sır BURAYA YAZILMAZ; "
        "ortam değişkenleriyle verilir (docs/bildirimler.md).",
        "deneme_modu": True,
        "bildirilen_durumlar": [x for x in ("İzlemede", "Kritik") if x in s["bd_durumlar"]],
        "iyilesme_bildir": bool(s["bd_iyilesme"]),
        "cooldown_saat": sade(s["bd_cooldown"]),
        "surekli_saat": {"İzlemede": int(s["bd_surekli_izlemede"]), "Kritik": int(s["bd_surekli_kritik"])},
        "iyilesme_saat": int(s["bd_iyilesme_saat"]),
        "min_guven": round(s["bd_min_guven"] / 100, 2),
        "hatirlatma": bool(s["bd_hatirlatma"]),
        "en_eski_olay_saat": sade(s["bd_eski_saat"]) if s["bd_eski_sinirli"] else None,
    }
    if s["bd_sessiz"]:
        d["sessiz_saatler"] = {
            "aktif": True,
            "baslangic": f"{s['bd_sessiz_bas']:%H:%M}",
            "bitis": f"{s['bd_sessiz_bit']:%H:%M}",
            "kritik_haric": bool(s["bd_sessiz_kritik"]),
        }
    if s["bd_birim_adlari"]:
        d["birim_adlari"] = s["bd_birim_adlari"]

    sunucu = s["bd_smtp_sunucu"].strip()
    eposta = {
        "aktif": bool(s["bd_eposta"]),
        "sunucu": sunucu,
        "port": int(s["bd_smtp_port"]) if sunucu else "",
        "guvenlik": s["bd_smtp_guv"] if sunucu else "",
        "kullanici": s["bd_smtp_kullanici"].strip(),
        "gonderen": s["bd_smtp_gonderen"].strip(),
        "alicilar": satirlar(s["bd_smtp_alicilar"]),
    }
    telegram = {"aktif": bool(s["bd_telegram"]), "sohbet_idleri": satirlar(s["bd_tg_idler"])}
    webhook = {"aktif": bool(s["bd_webhook"]), "url": s["bd_wh_url"].strip()}
    d["kanallar"] = {
        "konsol": {"aktif": bool(s["bd_konsol"])},
        # Boş alanlar yazılmaz; etkin bir kanalda eksik kalırlarsa doğrulama bunu Türkçe bildirir
        **{
            ad: {a: v for a, v in c.items() if a == "aktif" or v not in ("", [])}
            for ad, c in (("eposta", eposta), ("telegram", telegram), ("webhook", webhook))
        },
    }
    return d


def onizle(kurallar, hourly, adlar, now, tum_gecmis):
    """Kuralları demo filosunun saatlik tahminleri üzerinde DENEME modunda çalıştırır.

    Hiçbir kanala bağlanılmaz ve durum dosyası yazılmaz (durum_yolu=None).
    Döndürür: (rapor, mesajlar); mesajlar[i] = rapor.olaylar[i] için [(kanal, hedef, metin)].
    """
    k = replace(
        kurallar,
        deneme_modu=True,
        birim_adlari={**adlar, **kurallar.birim_adlari},
        en_eski_olay_saat=None if tum_gecmis else kurallar.en_eski_olay_saat,
    )
    cikti = []
    rapor = isle(
        hourly[hourly["timestamp"] <= now],
        k,
        durum_yolu=None,
        gonderici=Gonderici(k, deneme=True, cikti=cikti.append),
        simdi=now,
    )
    cikti = iter(cikti)  # deneme modunda her kanal sonucu için tek bir çıktı üretilir, aynı sırada
    mesajlar = []
    for _, sonuclar in rapor.sonuclar:
        olay_mesajlari = []
        for r in sonuclar:
            if r.deneme:
                _, _, govde = next(cikti).partition("\n")  # ilk satır "[DENEME] kanal → hedef"
                olay_mesajlari.append(
                    (
                        r.kanal,
                        r.hedef,
                        "\n".join(s[4:] if s.startswith("    ") else s for s in govde.splitlines()),
                    )
                )
        mesajlar.append(olay_mesajlari)
    return rapor, mesajlar


# ---------------------------------------------------------------- Hazırlık
raw, hourly, units, metrics, model = load_demo(str(root()))
adlar = {u["unit_id"]: u["name"] for u in units}
min_h = int((hourly["timestamp"].min() - START) / pd.Timedelta(hours=1))
max_h = int((hourly["timestamp"].max() - START) / pd.Timedelta(hours=1))

# Başka sayfaya geçince widget değerleri silinir; form değerlerinin bir kopyası oturumda tutulur
saklanan = st.session_state.get("_bd_saklanan", {})
for ad, v in VARSAYILAN.items():
    if ad not in st.session_state:
        st.session_state[ad] = saklanan.get(ad, v)

st.title("🔔 Bildirim Ayarları")
st.info(
    "**Bu sayfa yalnızca ayar hazırlar ve deneme modunda önizler; buradan hiçbir mesaj gönderilmez.** "
    "Parola, token ve gizli anahtarlar panelde girilmez ya da saklanmaz: sunucunun ortam değişkenlerinden "
    "okunur. Hazırladığınız ayar dosyasını `predict.py --bildirim ayar.json` ile kullanın "
    f"([Bildirimler belgesi]({DOKUMAN}))."
)

# ---------------------------------------------------------------- Mevcut ayarı yükle
with st.expander("Var olan bir ayar dosyasını yükle ve düzenle"):
    yuklenen = st.file_uploader(
        "Ayar dosyası (JSON)",
        type=["json"],
        key="bd_yukleyici",
        help="Ayar dosyasında parola/token alanı olmamalıdır; olsa da forma alınmaz.",
    )
    if yuklenen is not None and st.session_state.get("bd_dosya_id") != yuklenen.file_id:
        st.session_state["bd_dosya_id"] = yuklenen.file_id
        st.session_state["bd_kaynak"] = (yuklenen.name, yuklenen.getvalue())
        st.session_state["bd_uygula"] = True
    if st.session_state.pop("bd_uygula", False):
        ad, veri = st.session_state["bd_kaynak"]
        try:
            for u in ayar_uygula(json.loads(veri.decode("utf-8-sig"))):
                st.warning(u)
            st.success(f"**{ad}** forma yüklendi; aşağıda düzenleyebilirsiniz.")
        except (AyarHatasi, ValueError) as e:
            st.error(f"Ayar dosyası kullanılamıyor ({ad}): {e}")

# ---------------------------------------------------------------- Form
sol, sag = st.columns(2, gap="large")
with sol:
    with st.container(border=True):
        st.markdown("**Ne zaman bildirilsin?**")
        st.multiselect(
            "Bildirilecek durumlar",
            ["İzlemede", "Kritik"],
            key="bd_durumlar",
            help="Seçilmeyen durum için uyarı üretilmez. İyileşme bildirimi bundan bağımsızdır.",
        )
        if not st.session_state["bd_durumlar"]:
            st.warning("Hiç durum seçilmedi: hiçbir uyarı üretilmez.")
        c = st.columns(2)
        c[0].checkbox(
            "İyileşmeyi bildir",
            key="bd_iyilesme",
            help="Normal'e dönüş kalıcı olursa (ve daha önce uyarı gittiyse) bildirilir.",
        )
        c[1].checkbox(
            "Hatırlatma gönder",
            key="bd_hatirlatma",
            help="Alarm sürerken bekleme süresi dolunca tekrar hatırlatır.",
        )
        st.slider(
            "Asgari model güveni (%)",
            0,
            100,
            step=5,
            key="bd_min_guven",
            help="Yükselme ve hatırlatma yalnızca o saatteki güven bu değer ve üzerindeyse bildirilir.",
        )
    with st.container(border=True):
        st.markdown("**Tekrar ve süreklilik**")
        st.number_input(
            "Bekleme süresi / cooldown (saat)",
            min_value=0.0,
            step=1.0,
            key="bd_cooldown",
            help="Aynı ünite, durum ve arıza türü için bu süre içinde ikinci mesaj gitmez.",
        )
        c = st.columns(3)
        c[0].number_input(
            "İzlemede (saat)",
            min_value=1,
            step=1,
            key="bd_surekli_izlemede",
            help="Durumun kalıcı sayılması için gereken ardışık saat.",
        )
        c[1].number_input(
            "Kritik (saat)",
            min_value=1,
            step=1,
            key="bd_surekli_kritik",
            help="Durumun kalıcı sayılması için gereken ardışık saat.",
        )
        c[2].number_input(
            "İyileşme (saat)",
            min_value=1,
            step=1,
            key="bd_iyilesme_saat",
            help="Normal'in kalıcı sayılması için gereken ardışık saat.",
        )
        st.caption(
            "Süreklilik: durum bu kadar saat üst üste sürmeden bildirim üretilmez (anlık dalgalanma elenir)."
        )
        c = st.columns([1, 1])
        c[0].checkbox(
            "Eski olayları sınırla",
            key="bd_eski_sinirli",
            help="İşaretliyse belirtilen saatten eski geçişler gönderilmez (ilk çalıştırmada eski "
            "mesaj yağmasını önler). İşareti kaldırırsanız sınır yoktur.",
        )
        c[1].number_input(
            "Sınır (saat)",
            min_value=0.0,
            step=6.0,
            key="bd_eski_saat",
            disabled=not st.session_state["bd_eski_sinirli"],
        )
    with st.container(border=True):
        st.markdown("**Sessiz saatler**")
        st.checkbox(
            "Sessiz saatleri kullan",
            key="bd_sessiz",
            help="Bu aralıkta üretilen olaylar ertelenir ve aralık bitince gönderilir.",
        )
        pasif = not st.session_state["bd_sessiz"]
        c = st.columns(2)
        c[0].time_input("Başlangıç", key="bd_sessiz_bas", step=900, disabled=pasif)
        c[1].time_input("Bitiş", key="bd_sessiz_bit", step=900, disabled=pasif)
        st.checkbox("Kritik uyarılar sessiz saatte de hemen gitsin", key="bd_sessiz_kritik", disabled=pasif)

with sag:
    with st.container(border=True):
        st.markdown("**Kanallar**")
        st.checkbox("Konsol (komut satırı çıktısı)", key="bd_konsol")
        st.checkbox("E-posta", key="bd_eposta")
        if st.session_state["bd_eposta"] or st.session_state["bd_smtp_sunucu"]:
            ep = not st.session_state["bd_eposta"]
            c = st.columns([3, 1, 2])
            c[0].text_input(
                "SMTP sunucusu", key="bd_smtp_sunucu", placeholder="smtp.example.com", disabled=ep
            )
            c[1].number_input("Port", min_value=1, max_value=65535, step=1, key="bd_smtp_port", disabled=ep)
            c[2].selectbox(
                "Güvenlik",
                ["starttls", "ssl"],
                key="bd_smtp_guv",
                disabled=ep,
                format_func={"starttls": "STARTTLS", "ssl": "SSL/TLS"}.get,
            )
            c = st.columns(2)
            c[0].text_input(
                "Kullanıcı adı",
                key="bd_smtp_kullanici",
                placeholder="uyari@example.com",
                disabled=ep,
                help="Parola buraya girilmez; SOGUTMA_SMTP_PAROLA ortam değişkeninden okunur.",
            )
            c[1].text_input(
                "Gönderen",
                key="bd_smtp_gonderen",
                placeholder="Soğutma İzleme <uyari@example.com>",
                disabled=ep,
            )
            st.text_area(
                "Alıcılar (her satıra bir adres)",
                key="bd_smtp_alicilar",
                height=80,
                placeholder="bakim@example.com",
                disabled=ep,
            )
        st.checkbox("Telegram", key="bd_telegram")
        if st.session_state["bd_telegram"] or st.session_state["bd_tg_idler"]:
            st.text_area(
                "Sohbet kimlikleri (her satıra bir kimlik)",
                key="bd_tg_idler",
                height=80,
                placeholder="-1001234567890",
                disabled=not st.session_state["bd_telegram"],
                help="Bot token'ı buraya girilmez; SOGUTMA_TELEGRAM_TOKEN ortam değişkeninden okunur.",
            )
        st.checkbox("Webhook", key="bd_webhook")
        if st.session_state["bd_webhook"] or st.session_state["bd_wh_url"]:
            st.text_input(
                "Webhook adresi (http / https)",
                key="bd_wh_url",
                placeholder="https://example.com/hooks/sogutma",
                disabled=not st.session_state["bd_webhook"],
                help="Adres gizli anahtar içeriyorsa burayı boş bırakıp SOGUTMA_WEBHOOK_URL kullanın.",
            )

    with st.container(border=True):
        st.markdown("**Gizli bilgiler: ortam değişkenleri**")
        st.caption(
            "Sunucuda tanımlı olmaları gerekir; değerleri burada gösterilmez ve saklanmaz. "
            "Parola (kullanıcı adı varsa) ve Telegram token'ı zorunludur, diğerleri isteğe bağlıdır."
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Değişken": ad,
                        "Kanal": kanal,
                        "Durum": "✅ tanımlı" if os.environ.get(ad) else "⚪ tanımlı değil",
                    }
                    for ad, kanal in ORTAM
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        s = st.session_state
        if s["bd_eposta"] and s["bd_smtp_kullanici"].strip() and not os.environ.get(ENV_SMTP_PAROLA):
            st.warning(
                f"E-posta etkin ve kullanıcı adı var ama `{ENV_SMTP_PAROLA}` tanımlı değil: "
                "gerçek gönderimde bu kanal başarısız olur."
            )
        if s["bd_telegram"] and not os.environ.get(ENV_TELEGRAM_TOKEN):
            st.warning(
                f"Telegram etkin ama `{ENV_TELEGRAM_TOKEN}` tanımlı değil: "
                "gerçek gönderimde bu kanal başarısız olur."
            )
        if s["bd_webhook"] and not s["bd_wh_url"].strip() and not os.environ.get(ENV_WEBHOOK_URL):
            st.warning(f"Webhook adresi ne ayarda ne de `{ENV_WEBHOOK_URL}` ortam değişkeninde var.")

st.session_state["_bd_saklanan"] = {ad: st.session_state.get(ad, v) for ad, v in VARSAYILAN.items()}

# ---------------------------------------------------------------- Doğrulama ve indirme
st.subheader("Doğrulama ve indirme")
cfg = ayar_olustur()
try:
    kurallar = Kurallar.from_dict(cfg)  # sogutma.bildirim'in kendi yükleyicisi
    st.success("Ayarlar geçerli.")
except AyarHatasi as e:
    kurallar = None
    st.error(f"Ayarlar geçersiz: {e}")
metin = json.dumps(cfg, ensure_ascii=False, indent=2)
st.download_button(
    "Ayar dosyasını indir (JSON)",
    metin,
    file_name="bildirim_ayarlari.json",
    mime="application/json",
    icon="📥",
    disabled=kurallar is None,
    help="Dosyada her zaman deneme_modu: true yazar; gerçek gönderim yalnızca "
    "`predict.py --gonder` ile yapılır.",
)
with st.expander("Ayar dosyasının içeriği"):
    st.code(metin, language="json")
    st.caption(
        "Kullanım: `python predict.py veri.csv --bildirim bildirim_ayarlari.json` (deneme modu); "
        "gerçek gönderim için `--gonder` eklenir."
    )

# ---------------------------------------------------------------- Önizleme
st.subheader("Önizleme (deneme modu)")
t_now = min(max(int(st.session_state.get("_t_son", max_h)), min_h), max_h)  # Filo sayfasındaki seçim
now = START + pd.Timedelta(hours=t_now)
st.caption(
    f"Kurallar, demo filosunun saatlik tahminleri üzerinde **{fmt_time(now)}** anına kadar çalıştırılır "
    "(Filo İzleme sayfasındaki simülasyon zamanı). **Hiçbir mesaj gönderilmez, durum dosyası yazılmaz.**"
)
st.page_link("sayfalar/filo.py", label="Simülasyon zamanını Filo İzleme sayfasından değiştirin", icon="🏭")

if kurallar is None:
    st.info("Önizleme için önce ayarlardaki hatayı düzeltin.")
else:
    tum = st.checkbox(
        "Geçmişteki tüm olayları göster",
        value=True,
        key="bd_onizleme_tum",
        help="İşaretliyse 'eski olayları sınırla' ayarı önizlemede yok sayılır; demo senaryosunun tamamını "
        "görürsünüz. İşareti kaldırırsanız yalnızca ayardaki sınır içindeki olaylar görünür.",
    )
    rapor, mesajlar = onizle(kurallar, hourly, adlar, now, tum)
    n_mesaj = sum(len(m) for m in mesajlar)
    c = st.columns(3)
    c[0].metric("Üretilen olay", len(rapor.olaylar))
    c[1].metric("Gönderilecek mesaj", n_mesaj, help="Olay sayısı × etkin kanal sayısı")
    c[2].metric("Ertelenen (sessiz saat)", len(rapor.ertelenen))
    if not (rapor.olaylar or rapor.ertelenen):
        st.info(
            "Bu ayarlarla, seçili zamana kadar bildirim üretilmedi. Kenar çubuğundan (Filo İzleme) zamanı "
            "ileri alın ya da kuralları gevşetin."
        )
    else:

        def satir(o, durum):
            return {
                "Zaman": fmt_time(pd.Timestamp(o.zaman)),
                "_ts": pd.Timestamp(o.zaman),
                "Ünite": o.ad,
                "Tür": TUR_ADLARI.get(o.tur, o.tur),
                "Durum": f"{STATUS_STYLE[o.durum][0]} {o.durum}",
                "Olası arıza": fault_name(o.ariza) if o.ariza and o.ariza != "normal" else "—",
                "Güven": "—" if o.guven is None else f"%{o.guven * 100:.0f}",
                "Sonuç": durum,
            }

        tablo = pd.DataFrame(
            [satir(o, "gönderilirdi") for o in rapor.olaylar]
            + [satir(o, "ertelenirdi (sessiz saat)") for o in rapor.ertelenen]
        )
        tablo = tablo.sort_values("_ts", ascending=False).drop(columns="_ts")
        st.dataframe(tablo, hide_index=True, width="stretch", height=min(35 * (len(tablo) + 1) + 3, 500))

        if rapor.olaylar:
            sirali = sorted(range(len(rapor.olaylar)), key=lambda i: rapor.olaylar[i].zaman, reverse=True)
            secim = st.selectbox(
                "Mesajını görmek istediğiniz olay",
                sirali,
                key="bd_onizleme_olay",
                format_func=lambda i: (
                    f"{fmt_time(pd.Timestamp(rapor.olaylar[i].zaman))} · "
                    f"{rapor.olaylar[i].ad} · {rapor.olaylar[i].durum}"
                ),
            )
            for kanal, hedef, govde in mesajlar[secim]:
                with st.container(border=True):
                    st.markdown(f"**{KANAL_ADLARI[kanal]}**" + ("" if hedef == "-" else f" → {hedef}"))
                    st.code(govde, language=None, wrap_lines=True)
            if not mesajlar[secim]:
                st.warning("Etkin kanal yok: mesaj gösterilemiyor. Bir kanal etkinleştirin.")
