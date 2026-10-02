"""Bildirim kuralları ve kanalları: tahmin çıktısından uyarı olayları üretir, gönderir.

Akış:  tahmin tablosu → olaylari_uret (geçişler, bekleme/cooldown, süreklilik) → Gonderici
(konsol / e-posta / Telegram / webhook) → durum dosyası (tekrar gönderimi önler).

Gizli bilgiler (SMTP parolası, Telegram token'ı, webhook sırrı) yalnızca ortam
değişkenlerinden okunur; ayar dosyasında bulunmaz. Ayrıntı: docs/bildirimler.md
"""

import hashlib
import hmac
import json
import os
import smtplib
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field, fields
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import pandas as pd

from .faults import FAULTS

DURUMLAR = ("Normal", "İzlemede", "Kritik")
SEVIYE = {d: i for i, d in enumerate(DURUMLAR)}
KANALLAR = ("konsol", "eposta", "telegram", "webhook")

ENV_SMTP_PAROLA = "SOGUTMA_SMTP_PAROLA"
ENV_SMTP_KULLANICI = "SOGUTMA_SMTP_KULLANICI"
ENV_TELEGRAM_TOKEN = "SOGUTMA_TELEGRAM_TOKEN"
ENV_WEBHOOK_URL = "SOGUTMA_WEBHOOK_URL"
ENV_WEBHOOK_SIRRI = "SOGUTMA_WEBHOOK_SIRRI"
IMZA_BASLIGI = "X-Sogutma-Imza"

MAX_TEKRAR = 3  # tüm kanallar başarısız olan olay en fazla bu kadar çalıştırmada denenir


class AyarHatasi(ValueError):
    """Ayar dosyası geçersiz (mesaj Türkçe ve kullanıcıya gösterilebilir)."""


# --------------------------------------------------------------------------- ayarlar


@dataclass
class SessizSaatler:
    aktif: bool = False
    baslangic: str = "22:00"
    bitis: str = "07:00"
    kritik_haric: bool = True  # Kritik uyarılar sessiz saatte de hemen gider

    def _dk(self, s):
        try:
            h, m = str(s).split(":")
            h, m = int(h), int(m)
            if not (0 <= h < 24 and 0 <= m < 60):
                raise ValueError
        except ValueError:
            raise AyarHatasi(f"sessiz_saatler: '{s}' geçerli bir SS:DD saati değil.") from None
        return h * 60 + m

    def dogrula(self):
        if self._dk(self.baslangic) == self._dk(self.bitis):
            raise AyarHatasi("sessiz_saatler: başlangıç ve bitiş aynı olamaz.")

    def icinde(self, ts) -> bool:
        if not self.aktif:
            return False
        t = pd.Timestamp(ts)
        m = t.hour * 60 + t.minute
        b, e = self._dk(self.baslangic), self._dk(self.bitis)
        return b <= m < e if b < e else (m >= b or m < e)


@dataclass
class Kurallar:
    deneme_modu: bool = True
    bildirilen_durumlar: Tuple[str, ...] = ("İzlemede", "Kritik")
    iyilesme_bildir: bool = True
    cooldown_saat: float = 12.0
    surekli_saat: Dict[str, int] = field(default_factory=lambda: {"İzlemede": 6, "Kritik": 3})
    iyilesme_saat: int = 6
    min_guven: float = 0.5
    hatirlatma: bool = False
    en_eski_olay_saat: Optional[float] = 48.0
    sessiz_saatler: SessizSaatler = field(default_factory=SessizSaatler)
    birim_adlari: Dict[str, str] = field(default_factory=dict)
    kanallar: Dict[str, dict] = field(default_factory=lambda: {"konsol": {"aktif": True}})

    @classmethod
    def from_dict(cls, d: dict) -> "Kurallar":
        if not isinstance(d, dict):
            raise AyarHatasi("Ayar dosyasının kökü bir nesne ({...}) olmalıdır.")
        d = {k: v for k, v in d.items() if not k.startswith("_")}  # "_aciklama" gibi notlar
        bilinen = {f.name for f in fields(cls)}
        yabanci = sorted(set(d) - bilinen)
        if yabanci:
            gecerli = ", ".join(sorted(bilinen))
            raise AyarHatasi(f"Bilinmeyen ayar(lar): {', '.join(yabanci)}. Geçerli anahtarlar: {gecerli}")
        k = cls()
        for ad in ("deneme_modu", "iyilesme_bildir", "hatirlatma"):
            if ad in d:
                if not isinstance(d[ad], bool):
                    raise AyarHatasi(f"'{ad}' true/false olmalıdır.")
                setattr(k, ad, d[ad])
        k.cooldown_saat = _sayi(d, "cooldown_saat", k.cooldown_saat, minimum=0)
        k.min_guven = _sayi(d, "min_guven", k.min_guven, minimum=0, maksimum=1)
        k.iyilesme_saat = int(_sayi(d, "iyilesme_saat", k.iyilesme_saat, minimum=1))
        if "en_eski_olay_saat" in d:
            v = d["en_eski_olay_saat"]
            k.en_eski_olay_saat = None if v is None else _sayi(d, "en_eski_olay_saat", 48, minimum=0)
        if "bildirilen_durumlar" in d:
            bd = d["bildirilen_durumlar"]
            if not isinstance(bd, list) or any(s not in ("İzlemede", "Kritik") for s in bd):
                raise AyarHatasi('\'bildirilen_durumlar\' yalnızca "İzlemede" ve "Kritik" içerebilir.')
            k.bildirilen_durumlar = tuple(bd)
        if "surekli_saat" in d:
            s = d["surekli_saat"]
            if isinstance(s, (int, float)) and not isinstance(s, bool):
                s = {"İzlemede": s, "Kritik": s}
            if not isinstance(s, dict) or set(s) - {"İzlemede", "Kritik"}:
                raise AyarHatasi('\'surekli_saat\' bir sayı ya da {"İzlemede": n, "Kritik": n} olmalıdır.')
            k.surekli_saat = {**k.surekli_saat, **{a: int(_sayi(s, a, 1, minimum=1)) for a in s}}
        if "sessiz_saatler" in d:
            s = d["sessiz_saatler"]
            ss = SessizSaatler()
            if not isinstance(s, dict) or set(s) - {f.name for f in fields(SessizSaatler)}:
                raise AyarHatasi(
                    "'sessiz_saatler' geçersiz: aktif, baslangic, bitis, kritik_haric kullanılabilir."
                )
            for a, v in s.items():
                setattr(ss, a, v)
            ss.dogrula()
            k.sessiz_saatler = ss
        if "birim_adlari" in d:
            if not isinstance(d["birim_adlari"], dict):
                raise AyarHatasi('\'birim_adlari\' {"A1": "Soğuk Oda A1"} biçiminde olmalıdır.')
            k.birim_adlari = {str(a): str(b) for a, b in d["birim_adlari"].items()}
        if "kanallar" in d:
            k.kanallar = _kanallari_dogrula(d["kanallar"])
        return k

    def birim_adi(self, uid) -> str:
        return self.birim_adlari.get(str(uid), f"Ünite {uid}")

    def esik(self, durum) -> int:
        return self.surekli_saat[durum]


def _sayi(d, ad, varsayilan, minimum=None, maksimum=None):
    v = d.get(ad, varsayilan)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise AyarHatasi(f"'{ad}' bir sayı olmalıdır.")
    if (minimum is not None and v < minimum) or (maksimum is not None and v > maksimum):
        aralik = f"{minimum} – {maksimum}" if maksimum is not None else f"en az {minimum}"
        raise AyarHatasi(f"'{ad}' geçersiz ({v}); beklenen: {aralik}.")
    return v


def _liste(v):
    return isinstance(v, list) and len(v) > 0


def _kanallari_dogrula(kanallar) -> Dict[str, dict]:
    if not isinstance(kanallar, dict):
        raise AyarHatasi("'kanallar' bir nesne olmalıdır.")
    yabanci = sorted(set(kanallar) - set(KANALLAR))
    if yabanci:
        raise AyarHatasi(f"Bilinmeyen kanal(lar): {', '.join(yabanci)}. Geçerli: {', '.join(KANALLAR)}")
    for ad, c in kanallar.items():
        if not isinstance(c, dict):
            raise AyarHatasi(f"kanallar.{ad} bir nesne olmalıdır.")
        if not c.get("aktif", False):
            continue
        eksik = []
        if ad == "eposta":
            eksik = [a for a in ("sunucu", "gonderen") if not c.get(a)]
            if not _liste(c.get("alicilar")):
                eksik.append("alicilar")
        elif ad == "telegram":
            if not _liste(c.get("sohbet_idleri")):
                eksik.append("sohbet_idleri")
        elif ad == "webhook":
            if not c.get("url") and not os.environ.get(ENV_WEBHOOK_URL):
                eksik.append(f"url (ya da {ENV_WEBHOOK_URL})")
            elif c.get("url") and not str(c["url"]).startswith(("http://", "https://")):
                raise AyarHatasi("kanallar.webhook.url http:// ya da https:// ile başlamalıdır.")
        if eksik:
            raise AyarHatasi(f"kanallar.{ad} etkin ama eksik alan(lar): {', '.join(eksik)}")
    return kanallar


def ayar_yukle(yol) -> Kurallar:
    """JSON (ya da tomllib/tomli varsa TOML) ayar dosyasını okur."""
    yol = Path(yol)
    try:
        if yol.suffix.lower() == ".toml":
            try:
                import tomllib as toml  # Python 3.11+
            except ImportError:
                try:
                    import tomli as toml
                except ImportError:
                    raise AyarHatasi("TOML için Python 3.11+ ya da 'tomli' gerekir; JSON kullanın.") from None
            with open(yol, "rb") as f:
                d = toml.load(f)
        else:
            d = json.loads(yol.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise AyarHatasi(f"Ayar dosyası bulunamadı: {yol}") from None
    except ValueError as e:
        if isinstance(e, AyarHatasi):
            raise
        raise AyarHatasi(f"Ayar dosyası okunamadı ({yol}): {e}") from None
    return Kurallar.from_dict(d)


# --------------------------------------------------------------------------- olaylar


@dataclass
class Olay:
    tur: str  # uyari | kritik | yukselis | iyilesme | hatirlatma | ariza_degisti
    birim: str
    ad: str
    zaman: str  # ISO
    durum: str
    onceki: str
    ariza: Optional[str] = None
    guven: Optional[float] = None
    saglik: Optional[float] = None
    eta_h: Optional[float] = None
    tekrar: int = 0

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in d.items() if k in {f.name for f in fields(cls)}})


def _bos_birim():
    return {"son_ts": None, "seviye": 0, "ariza": None, "acik": False, "gonderim": {}}


def bos_durum():
    return {"surum": 1, "birimler": {}, "bekleyen": []}


def durum_yukle(yol) -> Tuple[dict, Optional[str]]:
    """Durum dosyasını okur. Bozuksa .bozuk olarak saklanır, temiz durumla devam edilir."""
    if yol is None or not Path(yol).exists():
        return bos_durum(), None
    yol = Path(yol)
    try:
        d = json.loads(yol.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or not isinstance(d.get("birimler"), dict):
            raise ValueError("biçim")
        d.setdefault("bekleyen", [])
        return d, None
    except (ValueError, OSError):
        yedek = yol.with_name(yol.name + ".bozuk")
        try:
            os.replace(yol, yedek)
        except OSError:
            pass
        return bos_durum(), f"Durum dosyası bozuktu, {yedek.name} olarak saklandı; temiz durumla başlandı."


def durum_kaydet(durum, yol):
    yol = Path(yol)
    yol.parent.mkdir(parents=True, exist_ok=True)
    gecici = yol.with_name(yol.name + ".tmp")
    gecici.write_text(json.dumps(durum, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(gecici, yol)


def olaylari_uret(pred: pd.DataFrame, kurallar: Kurallar, durum: dict, simdi=None) -> List[Olay]:
    """Saatlik tahminlerden bildirim olayları üretir; `durum` yerinde güncellenir.

    Kurallar (ünite başına, saatlik satırlar sırayla işlenir):
      - Durum ancak `surekli_saat` saat üst üste sürerse kalıcı sayılır (Kritik sayacı
        İzlemede sayacını da besler: Kritik saatleri "en az İzlemede" sayılır).
      - Yükselme (Normal→İzlemede/Kritik, İzlemede→Kritik) güven >= min_guven ise olay üretir.
      - Aynı (ünite, durum, arıza) için `cooldown_saat` içinde ikinci olay üretilmez.
      - Normal'e dönüş `iyilesme_saat` sürerse ve daha önce uyarı gittiyse "iyilesme" olayı doğar.
      - Durumdaki son işlenen zamandan eski satırlar atlanır (tekrar çalıştırma güvenlidir).
    """
    gerekli = {"unit_id", "timestamp", "status", "pred_fault", "confidence"}
    if not gerekli <= set(pred.columns):
        raise ValueError(f"Tahmin tablosunda eksik sütun: {', '.join(sorted(gerekli - set(pred.columns)))}")
    if pred.empty:
        return []
    pred = pred.sort_values(["unit_id", "timestamp"], kind="stable")
    simdi = pd.Timestamp(simdi) if simdi is not None else pd.Timestamp(pred["timestamp"].max())
    cd = pd.Timedelta(hours=kurallar.cooldown_saat)
    eski = None if kurallar.en_eski_olay_saat is None else pd.Timedelta(hours=kurallar.en_eski_olay_saat)
    olaylar: List[Olay] = []

    for uid, g in pred.groupby("unit_id", sort=False):
        uid = str(uid)
        st = durum["birimler"].setdefault(uid, _bos_birim())
        son = pd.Timestamp(st["son_ts"]) if st["son_ts"] else None
        r0 = r1 = r2 = rf = 0
        onceki_ariza = None

        def yay(row, tur, durum_adi, ariza_adi, onceki_durum, st=st, uid=uid):
            """Durum filtresi, eskilik ve cooldown'dan geçerse olay ekler; eklendiyse True."""
            t = pd.Timestamp(row.timestamp)
            if eski is not None and simdi - t > eski:
                return False
            if tur != "iyilesme" and durum_adi not in kurallar.bildirilen_durumlar:
                return False
            anahtar = f"{durum_adi}|{ariza_adi}"
            son_g = st["gonderim"].get(anahtar)
            if son_g and t - pd.Timestamp(son_g) < cd:
                return False
            st["gonderim"] = {a: v for a, v in st["gonderim"].items() if t - pd.Timestamp(v) < cd}
            st["gonderim"][anahtar] = t.isoformat()
            guven = float(row.confidence)
            olaylar.append(_olay(row, tur, uid, kurallar, durum_adi, onceki_durum, ariza_adi, guven))
            return True

        row = None
        for row in g.itertuples(index=False):
            t = pd.Timestamp(row.timestamp)
            lv = SEVIYE[row.status]
            ariza = row.pred_fault
            r2 = r2 + 1 if lv == 2 else 0
            r1 = r1 + 1 if lv >= 1 else 0
            r0 = r0 + 1 if lv == 0 else 0
            rf = rf + 1 if ariza == onceki_ariza else 1
            onceki_ariza = ariza
            if son is not None and t <= son:
                continue
            st["son_ts"] = t.isoformat()
            guven = float(row.confidence)

            if r2 >= kurallar.esik("Kritik"):
                hedef = 2
            elif r1 >= kurallar.esik("İzlemede"):
                hedef = 1
            elif r0 >= kurallar.iyilesme_saat:
                hedef = 0
            else:
                continue
            L = st["seviye"]
            if hedef > L:
                if guven < kurallar.min_guven:
                    continue
                tur = ("uyari" if hedef == 1 else "kritik") if L == 0 else "yukselis"
                st["seviye"], st["ariza"] = hedef, ariza
                if yay(row, tur, DURUMLAR[hedef], ariza, DURUMLAR[L]):
                    st["acik"] = True
            elif hedef == 0 and L > 0:
                eski_ariza = st["ariza"]
                st["seviye"], st["ariza"] = 0, None
                if st["acik"] and kurallar.iyilesme_bildir:
                    yay(row, "iyilesme", "Normal", eski_ariza, DURUMLAR[L])
                st["acik"] = False
            elif 0 < hedef < L:
                st["seviye"] = hedef  # sessiz alçalma: sonraki yükselme yine bildirilebilsin
            elif hedef == L > 0 and guven >= kurallar.min_guven:
                degisti = ariza != st["ariza"] and ariza != "normal" and rf >= kurallar.esik(DURUMLAR[L])
                if degisti or (kurallar.hatirlatma and ariza == st["ariza"]):
                    tur = "ariza_degisti" if degisti else "hatirlatma"
                    if yay(row, tur, DURUMLAR[L], ariza, DURUMLAR[L]):
                        st["ariza"], st["acik"] = ariza, True
        # Süren ama hiç bildirilmemiş alarm (çok eski geçiş, cooldown vb.): güncel durum bir kez bildirilir
        if row is not None and st["seviye"] > 0 and not st["acik"]:
            lv = SEVIYE[row.status]
            if lv == st["seviye"] and float(row.confidence) >= kurallar.min_guven:
                if yay(row, "uyari" if lv == 1 else "kritik", row.status, row.pred_fault, "Normal"):
                    st["acik"], st["ariza"] = True, row.pred_fault
    return olaylar


def _olay(row, tur, uid, kurallar, durum, onceki, ariza, guven):
    def ayikla(v):
        return None if v is None or pd.isna(v) else float(v)

    alarm = durum != "Normal"
    return Olay(
        tur=tur,
        birim=uid,
        ad=kurallar.birim_adi(uid),
        zaman=pd.Timestamp(row.timestamp).isoformat(),
        durum=durum,
        onceki=onceki,
        ariza=ariza,
        guven=guven if alarm else None,
        saglik=ayikla(getattr(row, "health", None)),
        eta_h=ayikla(getattr(row, "eta_h", None)) if alarm else None,
    )


# --------------------------------------------------------------------------- mesajlar

_ETIKET = {"İzlemede": ("🟠", "İZLEMEDE"), "Kritik": ("🔴", "KRİTİK"), "Normal": ("🟢", "NORMALE DÖNDÜ")}
_NOT = {"yukselis": "yükseldi", "hatirlatma": "hatırlatma", "ariza_degisti": "arıza türü değişti"}


def eta_metni(h) -> str:
    if h is None or pd.isna(h):
        return "-"
    d, hh = divmod(int(round(h)), 24)
    if not d:
        return f"~{hh} saat"
    return f"~{d} gün" + (f" {hh} saat" if hh and d < 3 else "")


def _baslik(o: Olay) -> str:
    ikon, etiket = _ETIKET[o.durum]
    return f"{ikon} {etiket}" + (f" ({_NOT[o.tur]})" if o.tur in _NOT else "")


def _ariza_ad(o):
    return FAULTS[o.ariza]["ad"] if o.ariza in FAULTS else (o.ariza or "bilinmeyen")


def kisa_mesaj(o: Olay) -> str:
    """Tek satırlık biçim (Telegram, konsol, webhook özeti)."""
    parcalar = [_baslik(o), o.ad]
    if o.durum == "Normal":
        if o.ariza in FAULTS and o.ariza != "normal":
            parcalar.append(f"{_ariza_ad(o)} belirtisi geçti")
        if o.saglik is not None:
            parcalar.append(f"sağlık {o.saglik:.0f}/100")
        return " · ".join(parcalar)
    parcalar.append(f"{_ariza_ad(o)} (güven %{(o.guven or 0) * 100:.0f})")
    if o.eta_h is not None:
        parcalar.append(f"tahmini arıza {eta_metni(o.eta_h)}")
    if o.ariza in FAULTS:
        parcalar.append(f"Öneri: {FAULTS[o.ariza]['oneri']}")
    return " · ".join(parcalar)


def konu(o: Olay) -> str:
    etiket = _ETIKET[o.durum][1]
    return f"[Soğutma] {etiket} · {o.ad}" + ("" if o.durum == "Normal" else f" · {_ariza_ad(o)}")


def uzun_mesaj(o: Olay) -> str:
    """Çok satırlık düz metin (e-posta gövdesi)."""
    z = pd.Timestamp(o.zaman)
    satirlar = [
        f"{_baslik(o)} · {o.ad}",
        "",
        f"Zaman         : {z:%d.%m.%Y %H:%M}",
        f"Durum         : {o.onceki} → {o.durum}",
    ]
    if o.saglik is not None:
        satirlar.append(f"Sağlık skoru  : {o.saglik:.0f} / 100")
    if o.durum != "Normal":
        satirlar.append(f"Tahmini arıza : {_ariza_ad(o)} (güven %{(o.guven or 0) * 100:.0f})")
        if o.eta_h is not None:
            satirlar.append(f"Kalan süre    : {eta_metni(o.eta_h)} içinde arızalanabilir (kaba tahmin)")
        if o.ariza in FAULTS:
            satirlar.append(f"Belirtiler    : {FAULTS[o.ariza]['belirtiler']}")
            satirlar.append(f"Öneri         : {FAULTS[o.ariza]['oneri']}")
    satirlar += [
        "",
        "Bu ileti otomatik üretilmiştir; model çıktısı ön göstergedir, saha kontrolünün yerine geçmez.",
    ]
    return "\n".join(satirlar)


# --------------------------------------------------------------------------- kanallar


@dataclass
class Sonuc:
    kanal: str
    hedef: str
    ok: bool
    deneme: bool = False  # deneme modunda üretildi, gönderilmedi
    hata: Optional[str] = None
    deneme_sayisi: int = 0


class _KaliciHata(Exception):
    """Yeniden denemenin anlamsız olduğu hata (yanlış parola, 4xx yanıtı, eksik sır vb.)."""


def imza(govde: bytes, sir: str) -> str:
    """Webhook gövdesinin HMAC-SHA256 imzası ('sha256=<hex>')."""
    return "sha256=" + hmac.new(sir.encode("utf-8"), govde, hashlib.sha256).hexdigest()


class Gonderici:
    """Olayları etkin kanallara gönderir. Ağ hataları yakalanır, yeniden denenir, raporlanır."""

    def __init__(
        self,
        kurallar: Kurallar,
        deneme: Optional[bool] = None,
        ortam=None,
        cikti=print,
        uyku=time.sleep,
        max_deneme=3,
        bekleme_s=1.0,
        zaman_asimi=15,
    ):
        self.k = kurallar
        self.deneme = kurallar.deneme_modu if deneme is None else deneme
        self.ortam = os.environ if ortam is None else ortam
        self.cikti, self.uyku = cikti, uyku
        self.max_deneme, self.bekleme, self.zaman_asimi = max_deneme, bekleme_s, zaman_asimi

    def gonder(self, o: Olay) -> List[Sonuc]:
        sonuc = []
        for ad in KANALLAR:
            c = self.k.kanallar.get(ad)
            if not c or not c.get("aktif", False):
                continue
            try:
                sonuc += getattr(self, f"_{ad}")(o, c)
            except Exception as e:  # hiçbir kanal hattı durdurmamalı
                sonuc.append(Sonuc(ad, "-", False, False, self._temizle(f"{type(e).__name__}: {e}")))
        return sonuc

    def _temizle(self, metin: str) -> str:
        """Hata metninden gizli değerleri siler."""
        for a in (ENV_SMTP_PAROLA, ENV_TELEGRAM_TOKEN, ENV_WEBHOOK_SIRRI, ENV_WEBHOOK_URL):
            v = self.ortam.get(a)
            if v:
                metin = metin.replace(v, "***")
        return metin

    def _dene(self, kanal, hedef, fn) -> Sonuc:
        hata, n = None, 0
        for n in range(1, self.max_deneme + 1):
            try:
                fn()
                return Sonuc(kanal, hedef, True, False, None, n)
            except _KaliciHata as e:
                return Sonuc(kanal, hedef, False, False, self._temizle(str(e)), n)
            except (OSError, ValueError) as e:  # ağ/sunucu hataları (smtplib.SMTPException OSError'dır)
                hata = self._temizle(f"{type(e).__name__}: {e}")
            if n < self.max_deneme:
                self.uyku(self.bekleme * 2 ** (n - 1))
        return Sonuc(kanal, hedef, False, False, hata, n)

    def _kuru(self, kanal, hedef, metin) -> List[Sonuc]:
        self.cikti(f"[DENEME] {kanal} → {hedef}\n" + "\n".join("    " + s for s in metin.splitlines()))
        return [Sonuc(kanal, hedef, True, True)]

    def _konsol(self, o, c):
        if self.deneme:
            return self._kuru("konsol", "-", kisa_mesaj(o))
        self.cikti(kisa_mesaj(o))
        return [Sonuc("konsol", "-", True, False, None, 1)]

    def _eposta(self, o, c):
        alicilar = [str(a) for a in c["alicilar"]]
        hedef = ", ".join(alicilar)
        if self.deneme:
            return self._kuru("eposta", hedef, f"Konu: {konu(o)}\n\n{uzun_mesaj(o)}")
        kullanici = self.ortam.get(ENV_SMTP_KULLANICI) or c.get("kullanici")
        parola = self.ortam.get(ENV_SMTP_PAROLA)
        if kullanici and not parola:
            return [Sonuc("eposta", hedef, False, False, f"{ENV_SMTP_PAROLA} ortam değişkeni tanımlı değil.")]
        m = EmailMessage()
        m["Subject"], m["From"], m["To"] = konu(o), c["gonderen"], hedef
        m.set_content(uzun_mesaj(o))
        ssl_kip = c.get("guvenlik", "starttls") == "ssl"

        def gonder():
            host, port = c["sunucu"], int(c.get("port", 465 if ssl_kip else 587))
            bag = ssl.create_default_context()
            try:
                if ssl_kip:
                    s = smtplib.SMTP_SSL(host, port, timeout=self.zaman_asimi, context=bag)
                else:
                    s = smtplib.SMTP(host, port, timeout=self.zaman_asimi)
                with s as oturum:
                    if not ssl_kip:
                        oturum.ehlo()
                        oturum.starttls(context=bag)
                        oturum.ehlo()
                    if kullanici:
                        oturum.login(kullanici, parola)
                    oturum.send_message(m)
            except (
                smtplib.SMTPAuthenticationError,
                smtplib.SMTPRecipientsRefused,
                smtplib.SMTPSenderRefused,
            ) as e:
                raise _KaliciHata(f"{type(e).__name__}: {e}") from None

        return [self._dene("eposta", hedef, gonder)]

    def _telegram(self, o, c):
        metin = kisa_mesaj(o)[:4096]
        sohbetler = [str(i) for i in c["sohbet_idleri"]]
        if self.deneme:
            return self._kuru("telegram", ", ".join(sohbetler), metin)
        token = self.ortam.get(ENV_TELEGRAM_TOKEN)
        if not token:
            return [
                Sonuc("telegram", i, False, False, f"{ENV_TELEGRAM_TOKEN} ortam değişkeni tanımlı değil.")
                for i in sohbetler
            ]
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        sonuc = []
        for sohbet in sohbetler:
            govde = json.dumps({"chat_id": sohbet, "text": metin}).encode("utf-8")
            sonuc.append(self._dene("telegram", sohbet, lambda g=govde: self._post(url, g)))
        return sonuc

    def _webhook(self, o, c):
        govde = json.dumps(
            {"surum": 1, "olay": o.to_dict(), "mesaj": kisa_mesaj(o)}, ensure_ascii=False
        ).encode("utf-8")
        url = self.ortam.get(ENV_WEBHOOK_URL) or c.get("url", "")
        hedef = urlparse(url).netloc or "-"  # sorgu/yol gizli bilgi içerebilir
        sir = self.ortam.get(ENV_WEBHOOK_SIRRI)
        basliklar = {IMZA_BASLIGI: imza(govde, sir)} if sir else {}
        if self.deneme:
            return self._kuru("webhook", hedef, govde.decode("utf-8") + ("\n(HMAC imzalı)" if sir else ""))
        return [self._dene("webhook", hedef, lambda: self._post(url, govde, basliklar))]

    def _post(self, url, govde, basliklar=None):
        istek = urllib.request.Request(
            url,
            data=govde,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "sogutma-bildirim/1",
                **(basliklar or {}),
            },
        )
        try:
            with urllib.request.urlopen(istek, timeout=self.zaman_asimi) as yanit:
                kod = getattr(yanit, "status", 200)
                if not 200 <= kod < 300:
                    raise OSError(f"HTTP {kod}")
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 429:
                raise _KaliciHata(f"HTTP {e.code}") from None
            raise OSError(f"HTTP {e.code}") from None


# --------------------------------------------------------------------------- uçtan uca


@dataclass
class IsleRapor:
    olaylar: List[Olay] = field(default_factory=list)  # bu çalıştırmada gönderilen/denenen
    ertelenen: List[Olay] = field(default_factory=list)  # sessiz saat nedeniyle bekletilen
    sonuclar: List[Tuple[Olay, List[Sonuc]]] = field(default_factory=list)
    basarisiz: List[Olay] = field(default_factory=list)  # tüm kanallar başarısız; yeniden denenecek
    vazgecilen: List[Olay] = field(default_factory=list)
    uyarilar: List[str] = field(default_factory=list)
    deneme: bool = True

    def ozet(self) -> str:
        if not (self.olaylar or self.ertelenen or self.uyarilar):
            return "Bildirim: yeni olay yok."
        kip = "DENEME MODU, hiçbir şey gönderilmedi" if self.deneme else "gönderim açık"
        s = [f"Bildirim ({kip}): {len(self.olaylar)} olay"]
        if self.ertelenen:
            s.append(f"{len(self.ertelenen)} olay sessiz saat nedeniyle ertelendi")
        if self.basarisiz:
            s.append(f"{len(self.basarisiz)} olay gönderilemedi, sonraki çalıştırmada yeniden denenecek")
        if self.vazgecilen:
            s.append(f"{len(self.vazgecilen)} olaydan vazgeçildi ({MAX_TEKRAR} denemede gönderilemedi)")
        satirlar = ["; ".join(s) + "."]
        for _, sonuc in self.sonuclar:
            for r in sonuc:
                if not r.ok:
                    satirlar.append(f"  ! {r.kanal} ({r.hedef}) başarısız: {r.hata}")
        satirlar += [f"  ! {u}" for u in self.uyarilar]
        return "\n".join(satirlar)


def isle(
    pred, kurallar: Kurallar, durum_yolu=None, gonderici: Optional[Gonderici] = None, simdi=None
) -> IsleRapor:
    """Tahmin tablosundan olayları üretir, gönderir ve durum dosyasını günceller.

    Deneme modunda durum dosyasına YAZILMAZ (aynı olaylar sonraki gerçek çalıştırmada gönderilebilsin).
    `simdi` verilmezse tablodaki son zaman damgası kullanılır (sessiz saat kontrolü de buna göredir).
    """
    gonderici = gonderici or Gonderici(kurallar)
    rapor = IsleRapor(deneme=gonderici.deneme)
    if pred.empty:
        return rapor
    durum, uyari = durum_yukle(durum_yolu)
    if uyari:
        rapor.uyarilar.append(uyari)
    simdi = pd.Timestamp(simdi) if simdi is not None else pd.Timestamp(pred["timestamp"].max())
    bekleyenler = [Olay.from_dict(d) for d in durum.get("bekleyen", [])]
    yeni = olaylari_uret(pred, kurallar, durum, simdi)
    durum["bekleyen"] = []
    sessiz = kurallar.sessiz_saatler.icinde(simdi)
    for o in bekleyenler + yeni:
        if sessiz and not (kurallar.sessiz_saatler.kritik_haric and o.durum == "Kritik"):
            rapor.ertelenen.append(o)
            durum["bekleyen"].append(o.to_dict())
            continue
        sonuc = gonderici.gonder(o)
        rapor.olaylar.append(o)
        rapor.sonuclar.append((o, sonuc))
        if sonuc and not any(r.ok for r in sonuc):
            o.tekrar += 1
            if o.tekrar < MAX_TEKRAR:
                rapor.basarisiz.append(o)
                durum["bekleyen"].append(o.to_dict())
            else:
                rapor.vazgecilen.append(o)
    if not gonderici.deneme and durum_yolu:
        durum_kaydet(durum, durum_yolu)
    return rapor
