"""Tahmin servisi (REST API): CSV ya da JSON ölçümlerinden ünite başına arıza tahmini.

Çalıştırma:  uvicorn api:app --port 8000      (belgeler: http://localhost:8000/docs)
Ayrıntılar:  docs/api.md

Ortam değişkenleri:
  SOGUTMA_ROOT            veri/model klasörü (varsayılan: depo kökü)
  SOGUTMA_API_ANAHTARI    verilirse /saglik dışındaki uçlar X-API-Anahtari başlığı ister
  SOGUTMA_MAKS_YUKLEME_MB istek gövdesi üst sınırı (varsayılan 20)
"""

import io
import json
import logging
import math
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import joblib
import pandas as pd
from fastapi import Depends, FastAPI, File, Query, Request, Security, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, confloat, model_validator

from .analiz import YetersizVeri, analiz_et, analiz_et_tablo, son_durum
from .faults import FAULT_TYPES, FAULTS, fault_name
from .features import FEATURE_LABELS
from .ingest import ValidationError
from .model import MODEL_VERSION
from .simulator import TIP_TURLERI

log = logging.getLogger("sogutma.api")

SURUM = "1.0.0"
VARSAYILAN_YUKLEME_MB = 20
MAKS_OLCUM = 200_000  # tek JSON isteğindeki en çok ölçüm kaydı
SENTETIK_NOTU = (
    "Model sentetik (simülatör) veriyle eğitildi ve ölçülen performans sentetik veriye aittir; "
    "gerçek saha doğruluğu doğrulanmamıştır. Çıktıyı karar vermek için değil, ön gösterge olarak kullanın."
)
ZAMAN_BICIMI = "%Y-%m-%d %H:%M:%S"


def kok() -> Path:
    """Veri/model klasörü. SOGUTMA_ROOT değişkeni bunu değiştirir (testler ve Docker için)."""
    return Path(os.environ.get("SOGUTMA_ROOT") or Path(__file__).resolve().parent.parent)


def maks_yukleme_bayt() -> int:
    try:
        mb = float(os.environ.get("SOGUTMA_MAKS_YUKLEME_MB", VARSAYILAN_YUKLEME_MB))
    except ValueError:
        mb = VARSAYILAN_YUKLEME_MB
    if not math.isfinite(mb) or mb <= 0:
        mb = VARSAYILAN_YUKLEME_MB
    return int(mb * 1024 * 1024)


# ---------------------------------------------------------------------------------------------
# Hata modeli
# ---------------------------------------------------------------------------------------------


class Hata(BaseModel):
    hata: str = Field(description="Makine tarafından okunabilir hata kodu (ör. veri_gecersiz, model_yok).")
    mesaj: str = Field(description="Türkçe açıklama.")
    ayrintilar: List[str] = Field(default_factory=list, description="Varsa tek tek sorunların listesi.")


class ApiHatasi(Exception):
    def __init__(self, durum: int, kod: str, mesaj: str, ayrintilar: Optional[List[str]] = None):
        super().__init__(mesaj)
        self.durum, self.kod, self.mesaj, self.ayrintilar = durum, kod, mesaj, ayrintilar or []


def _hata_yaniti(durum: int, kod: str, mesaj: str, ayrintilar=None) -> JSONResponse:
    return JSONResponse(
        status_code=durum, content=dict(hata=kod, mesaj=mesaj, ayrintilar=list(ayrintilar or []))
    )


HATA_YANITLARI = {
    401: {"model": Hata, "description": "API anahtarı eksik ya da yanlış."},
    413: {"model": Hata, "description": "İstek gövdesi izin verilen boyutu aşıyor."},
    422: {"model": Hata, "description": "İstek ya da veri kullanılamıyor (Türkçe ayrıntılarla)."},
    503: {"model": Hata, "description": "Model yok, okunamıyor ya da eski sürümde (python train.py)."},
}

# ---------------------------------------------------------------------------------------------
# Model deposu: dosya değiştiğinde (yeniden eğitim) otomatik yeniden yüklenir
# ---------------------------------------------------------------------------------------------


class ModelDeposu:
    def __init__(self):
        self._kilit = threading.Lock()
        self._anahtar = None
        self._model = None
        self._hata: Optional[ApiHatasi] = None

    @staticmethod
    def yol() -> Path:
        return kok() / "models" / "predictor.joblib"

    def al(self):
        yol = self.yol()
        try:
            anahtar = (str(yol), yol.stat().st_mtime_ns)
        except OSError:
            raise ApiHatasi(
                503, "model_yok", f"Model bulunamadı ({yol}). Önce modeli eğitin:  python train.py"
            )
        with self._kilit:
            if anahtar != self._anahtar:
                self._anahtar, self._model, self._hata = anahtar, None, None
                try:
                    model = joblib.load(yol)
                except Exception as e:  # bozuk / uyumsuz dosya
                    log.warning("Model okunamadı: %s", e)
                    self._hata = ApiHatasi(
                        503,
                        "model_okunamadi",
                        f"Model dosyası okunamadı ({yol}). Yeniden eğitin:  python train.py",
                    )
                else:
                    if getattr(model, "version", 1) != MODEL_VERSION:
                        self._hata = ApiHatasi(
                            503,
                            "model_eski",
                            f"Model dosyası eski bir sürüme ait ({yol}). Yeniden eğitin:  python train.py",
                        )
                    else:
                        self._model = model
            if self._hata:
                raise self._hata
            return self._model


def model_al(request: Request):
    return request.app.state.depo.al()


# ---------------------------------------------------------------------------------------------
# Kimlik doğrulama (isteğe bağlı)
# ---------------------------------------------------------------------------------------------

_ANAHTAR_BASLIGI = APIKeyHeader(
    name="X-API-Anahtari",
    auto_error=False,
    description="SOGUTMA_API_ANAHTARI ayarlıysa zorunlu; ayarlı değilse yoksayılır.",
)


def yetki(anahtar: Optional[str] = Security(_ANAHTAR_BASLIGI)):
    beklenen = os.environ.get("SOGUTMA_API_ANAHTARI")
    if not beklenen:
        return
    if not anahtar or not secrets.compare_digest(anahtar.encode("utf-8"), beklenen.encode("utf-8")):
        raise ApiHatasi(401, "yetkisiz", "API anahtarı eksik ya da yanlış (X-API-Anahtari başlığı).")


# ---------------------------------------------------------------------------------------------
# Şemalar
# ---------------------------------------------------------------------------------------------

Sayi = confloat(allow_inf_nan=False)


class Olcum(BaseModel):
    """5 dakikalık tek bir ölçüm. Alanlar CSV şemasıyla aynıdır (docs/veri-formati.md)."""

    timestamp: datetime = Field(
        description="Ölçüm zamanı UTC (ISO 8601, ör. 2026-01-05T14:35:00Z); naif değer de UTC kabul edilir."
    )
    unit_id: str = Field("U1", description="Ünite / soğuk oda kimliği.", min_length=1, max_length=64)
    tip: Optional[str] = Field(None, description="Ekipman tipi: soguk_oda, dondurucu ya da market_dolabi.")
    t_amb: Optional[Sayi] = Field(
        ..., description="Dış ortam sıcaklığı (°C). Zorunlu alan; sensör okunamadıysa null."
    )
    t_room: Optional[Sayi] = Field(
        ..., description="Oda (kabin) sıcaklığı (°C). Zorunlu alan; sensör okunamadıysa null."
    )
    p_suc: Optional[Sayi] = Field(
        ..., description="Emme basıncı (bar, mutlak). Zorunlu alan; sensör okunamadıysa null."
    )
    p_dis: Optional[Sayi] = Field(
        ..., description="Basma basıncı (bar, mutlak). Zorunlu alan; sensör okunamadıysa null."
    )
    i_comp: Optional[Sayi] = Field(
        ..., description="Kompresör akımı (A). Zorunlu alan; sensör okunamadıysa null."
    )
    t_coil: Optional[Sayi] = Field(None, description="Evaporatör batarya / defrost sensörü sıcaklığı (°C).")
    sh: Optional[Sayi] = Field(None, description="Kızgınlık (K); yoksa t_suc ile türetilir.")
    sc: Optional[Sayi] = Field(None, description="Aşırı soğutma (K); yoksa t_liq ile türetilir.")
    t_suc: Optional[Sayi] = Field(None, description="Emme hattı sıcaklığı (°C); yalnızca sh türetmek için.")
    t_liq: Optional[Sayi] = Field(None, description="Sıvı hattı sıcaklığı (°C); yalnızca sc türetmek için.")
    i_fan: Optional[Sayi] = Field(None, description="Kondenser fan akımı (A).")
    vib: Optional[Sayi] = Field(None, description="Kompresör titreşimi (mm/s, RMS).")
    t_dis: Optional[Sayi] = Field(None, description="Basma hattı sıcaklığı (°C).")
    comp_on: Optional[bool] = Field(None, description="Kompresör çalışıyor mu; yoksa akımdan türetilir.")
    defrost: Optional[bool] = Field(None, description="Defrost aktif mi; yoksa hep kapalı varsayılır.")
    door_open: Optional[bool] = Field(None, description="Kapı açık mı (modelde kullanılmaz).")
    setpoint: Optional[Sayi] = Field(None, description="Termostat set değeri (°C).")


class OlcumIstegi(BaseModel):
    olcumler: List[Olcum] = Field(
        ...,
        min_length=1,
        max_length=MAKS_OLCUM,
        description="5 dakikalık ölçümler (bir ya da birden çok ünite). Ünite başına en az 24 saatlik "
        "geçerli veri gerekir; güvenilir sonuç için 3+ gün önerilir.",
    )
    setpoint: Optional[float] = Field(
        None, description="Kayıtlarda set değeri yoksa kullanılacak set değeri (°C)."
    )
    gauge: bool = Field(
        False, description="Basınçlar efektif (gauge) ölçülmüşse true; +1,013 bar ile mutlağa çevrilir."
    )
    tip: Optional[str] = Field(
        None, description="Kayıtlarda tip yoksa tüm üniteler için ekipman tipi (varsayılan soguk_oda)."
    )
    saatlik: bool = Field(False, description="true ise saatlik tahmin serisi de döner.")

    @model_validator(mode="after")
    def zaman_dilimleri_tutarlı(self):
        awareness = {m.timestamp.utcoffset() is not None for m in self.olcumler}
        if len(awareness) > 1:
            raise ValueError("Tüm timestamp değerleri ya saat dilimli ya da naif UTC olmalı; karıştırmayın.")
        return self


class Sapma(BaseModel):
    sinyal: str = Field(description="Sinyalin Türkçe adı.")
    deger: float = Field(description="Son saatteki değer.")
    normal: float = Field(description="Aynı ekipman tipinde normal çalışma ortalaması.")
    sapma_sigma: float = Field(description="Normalden sapma (standart sapma cinsinden, işaretli).")


class SensorSorunu(BaseModel):
    sensor: str = Field(description="Sorunlu sensörün kanonik alan adı (ör. p_suc).")
    neden: str = Field(description="Sensör kalite kodu (ör. takili, veri_kaybi, tutarsiz).")
    ilk: datetime = Field(description="Sorunun ilk görüldüğü saat.")
    son: datetime = Field(description="Sorunun son görüldüğü saat.")
    saat: int = Field(description="Bu kodla işaretlenen saat sayısı.")


class UniteDurumu(BaseModel):
    unit_id: str
    zaman: datetime = Field(description="Analizdeki son saat; UTC, yanıtta saat dilimi eklenmez.")
    saat_sayisi: int = Field(description="Analiz edilen saatlik pencere sayısı.")
    saglik: float = Field(description="Sağlık skoru, 0-100.")
    durum: str = Field(description="Normal, İzlemede ya da Kritik.")
    ariza: str = Field(
        description="Tahmini arıza kodu (normal, gaz_kacagi, kondenser_kirlenmesi, "
        "evaporator_buzlanma, kompresor_asinmasi, fan_arizasi)."
    )
    ariza_adi: str = Field(description="Tahmini arızanın Türkçe adı.")
    guven: float = Field(description="Tahminin güveni, 0-1.")
    kalan_saat: Optional[float] = Field(
        None, description="Arızaya kalan süre (saat, kaba tahmin); durum Normal ise null."
    )
    olasiliklar: Dict[str, float] = Field(description="Son saat için sınıf olasılıkları.")
    sapmalar: List[Sapma] = Field(description="Normalden en çok sapan sinyaller (açıklama).")
    oneri: str = Field(description="Bakım önerisi.")
    uyari_baslangic: Optional[datetime] = Field(
        None, description="Kesintisiz Normal dışı sürenin başlangıcı."
    )
    uyari_saat: int = Field(description="Kesintisiz Normal dışı saat sayısı (0 ise uyarı yok).")
    sensor_sorunu: bool = Field(False, description="Son saatte sensör kalite sorunu var mı.")
    sensor_notu: str = Field(
        "", description="Son saatteki sensör kalite uyarısı; ekipman arızasından ayrıdır."
    )
    sensor_sorunlari: List[SensorSorunu] = Field(
        default_factory=list, description="Analiz penceresinde ünite için özet sensör sorunları."
    )


class UniteVeriOzeti(BaseModel):
    bas: datetime
    bit: datetime
    saat: float = Field(description="Eksiksiz (tüm zorunlu sensörleri olan) veri süresi, saat.")
    kapsam: float = Field(description="Eksiksiz verinin oranı, 0-1.")
    ham_satir: int
    orneklem_dk: float = Field(description="Ortanca örnekleme aralığı (dakika).")


class VeriRaporu(BaseModel):
    bilgiler: List[str]
    uyarilar: List[str]
    turetilen: Dict[str, str] = Field(description="Girdide bulunmayıp türetilen/varsayılan alanlar.")
    eksik_sensorler: List[str]
    hesaplanamayan_oznitelikler: List[str] = Field(
        description="Sensör eksikliği yüzünden hiç hesaplanamayıp nötr sayılan sinyaller."
    )
    yinelenen_satir: int
    uniteler: Dict[str, UniteVeriOzeti]


class SaatlikKayit(BaseModel):
    zaman: datetime
    unit_id: str
    saglik: float
    durum: str
    ariza: str
    guven: float
    kalan_saat: Optional[float] = None
    sensor_sorunu: bool = False
    sensor_notu: str = ""


class TahminYaniti(BaseModel):
    model_surumu: int
    sentetik_uyari: str = Field(default=SENTETIK_NOTU)
    uniteler: List[UniteDurumu]
    rapor: VeriRaporu
    saatlik: Optional[List[SaatlikKayit]] = Field(None, description="Yalnızca saatlik=true ise.")


class Saglik(BaseModel):
    durum: str = Field(description="ok ya da model_yok / model_okunamadi / model_eski.")
    model_yuklu: bool
    model_surumu: Optional[int] = None
    calisma_suresi_sn: float
    kimlik_dogrulama: bool = Field(description="SOGUTMA_API_ANAHTARI ayarlı mı.")


class ArizaTuru(BaseModel):
    kod: str
    ad: str


class TipMetrigi(BaseModel):
    ornek_unite: Optional[int] = None
    saatlik_dogruluk: Optional[float] = None
    yakalama_orani: Optional[float] = None
    medyan_erken_uyari_gun: Optional[float] = None
    yanlis_alarm_unite: Optional[int] = None


class OrtamStresMetrigi(BaseModel):
    ortam_kaymasi_c: Optional[float] = None
    saatlik_dogruluk: Optional[float] = None
    makro_f1: Optional[float] = None
    yakalama_orani: Optional[float] = None
    medyan_erken_uyari_saat: Optional[float] = None
    yanlis_alarm_unite: Optional[int] = None


class MetrikOzeti(BaseModel):
    egitim_unite_sayisi: Optional[int] = None
    test_unite_sayisi: Optional[int] = None
    gun: Optional[int] = None
    saatlik_dogruluk: Optional[float] = None
    makro_f1: Optional[float] = None
    yakalama_orani: Optional[float] = Field(
        None, description="Arızayı gerçekleşmeden önce yakalama oranı, 0-1."
    )
    medyan_erken_uyari_gun: Optional[float] = None
    yanlis_alarm_unite: Optional[int] = None
    multiclass_brier: Optional[float] = Field(
        None, description="Kalibre edilmemiş olasılıklar için Brier skoru."
    )
    log_loss: Optional[float] = Field(None, description="Kalibre edilmemiş olasılıklar için log-loss.")
    eta_mae_saat: Optional[float] = Field(
        None,
        description=(
            "Arıza dışı sınıf tahmini ve pozitif kalan süre koşulundaki hata; "
            "tür eşleşmesi şart değildir."
        ),
    )
    eta_p90_mutlak_hata_saat: Optional[float] = None
    eta_dogru_tur_mae_saat: Optional[float] = Field(
        None, description="Tahmin edilen arıza türünün gerçek etiketle eşleştiği saatlerdeki ETA MAE."
    )
    eta_dogru_tur_p90_mutlak_hata_saat: Optional[float] = None
    tipe_gore: Dict[str, TipMetrigi] = Field(default_factory=dict)
    ortam_kaymasi_stresi: Dict[str, OrtamStresMetrigi] = Field(default_factory=dict)


class ModelBilgisi(BaseModel):
    model_surumu: int
    sentetik: bool = Field(True, description="Model ve metrikler sentetik veriye aittir.")
    uyari: str = Field(default=SENTETIK_NOTU)
    model_dosyasi_zamani: datetime
    ariza_turleri: List[ArizaTuru]
    ekipman_tipleri: List[str]
    metrikler: Optional[MetrikOzeti] = Field(None, description="models/metrics.json özeti (yoksa null).")


# ---------------------------------------------------------------------------------------------
# Dönüştürücüler
# ---------------------------------------------------------------------------------------------


def _sayi(v) -> Optional[float]:
    return None if v is None or pd.isna(v) else float(v)


def _zaman(ts) -> Optional[datetime]:
    return None if ts is None or pd.isna(ts) else pd.Timestamp(ts).to_pydatetime()


def _unite_durumu(analiz, uid, model) -> UniteDurumu:
    H, p = analiz.unite(uid)
    d = son_durum(p, H, model)
    son = p.iloc[-1]
    return UniteDurumu(
        unit_id=str(uid),
        zaman=_zaman(d["zaman"]),
        saat_sayisi=d["saat_sayisi"],
        saglik=d["health"],
        durum=d["status"],
        ariza=d["pred_fault"],
        ariza_adi=fault_name(d["pred_fault"]),
        guven=d["confidence"],
        kalan_saat=None if d["pred_fault"] == "normal" else _sayi(d["eta_h"]),
        olasiliklar={f: round(float(son[f"p_{f}"]), 4) for f in FAULT_TYPES},
        sapmalar=[
            Sapma(sinyal=a, deger=round(v, 3), normal=round(n, 3), sapma_sigma=round(z, 2))
            for a, v, n, z in d["sapmalar"]
        ],
        oneri=d["oneri"],
        uyari_baslangic=_zaman(d["uyari_baslangic"]),
        uyari_saat=d["uyari_saat"],
        sensor_sorunu=d["sensor_sorunu"],
        sensor_notu=d["sensor_notu"],
        sensor_sorunlari=[
            SensorSorunu(
                sensor=r.sensor, neden=r.neden, ilk=_zaman(r.ilk), son=_zaman(r.son), saat=int(r.saat)
            )
            for r in analiz.sensor_sorunlari(uid).itertuples()
        ],
    )


def _yanit(analiz, model, saatlik: bool) -> TahminYaniti:
    r = analiz.rapor
    rapor = VeriRaporu(
        bilgiler=r.bilgiler,
        uyarilar=r.uyarilar,
        turetilen=r.turetilen,
        eksik_sensorler=r.eksik_sensorler,
        hesaplanamayan_oznitelikler=[FEATURE_LABELS[f] for f in analiz.belirsiz],
        yinelenen_satir=r.kopya_satir,
        uniteler={
            str(u): UniteVeriOzeti(**{**v, "bas": _zaman(v["bas"]), "bit": _zaman(v["bit"])})
            for u, v in r.uniteler.items()
        },
    )
    seri = None
    if saatlik:
        t = analiz.rapor_tablosu()
        seri = [
            SaatlikKayit(
                zaman=_zaman(s.timestamp),
                unit_id=str(s.unit_id),
                saglik=s.health,
                durum=s.status,
                ariza=s.pred_fault,
                guven=s.confidence,
                kalan_saat=None if s.pred_fault == "normal" else _sayi(s.eta_h),
                sensor_sorunu=bool(s.sensor_sorunu),
                sensor_notu=str(s.sensor_notu or ""),
            )
            for s in t.itertuples()
        ]
    return TahminYaniti(
        model_surumu=MODEL_VERSION,
        uniteler=[_unite_durumu(analiz, u, model) for u in analiz.uniteler],
        rapor=rapor,
        saatlik=seri,
    )


def _calistir(is_, model, saatlik: bool) -> TahminYaniti:
    try:
        analiz = is_()
    except ValidationError as e:
        raise ApiHatasi(
            422,
            "veri_gecersiz",
            "Veri kullanılamıyor; ayrıntılar için 'ayrintilar' alanına bakın.",
            e.hatalar,
        )
    except YetersizVeri as e:
        raise ApiHatasi(422, "yetersiz_veri", str(e), [str(e)])
    return _yanit(analiz, model, saatlik)


def _tablo(olcumler: List[Olcum]) -> pd.DataFrame:
    """JSON ölçümlerini ingest'in beklediği metin sütunlu tabloya çevirir; tümüyle boş sütunlar atılır."""
    satirlar = []
    for o in olcumler:
        satir = {}
        for ad in Olcum.model_fields:
            v = getattr(o, ad)
            if v is None:
                satir[ad] = ""
            elif ad == "timestamp":
                utc = v.astimezone(timezone.utc).replace(tzinfo=None) if v.tzinfo else v
                satir[ad] = utc.strftime(ZAMAN_BICIMI)
            elif isinstance(v, bool):
                satir[ad] = "1" if v else "0"
            elif isinstance(v, float):
                satir[ad] = repr(v)
            else:
                satir[ad] = str(v)
        satirlar.append(satir)
    df = pd.DataFrame(satirlar, dtype=str)
    return df.loc[:, (df != "").any()]


def _metrik_ozeti(r: Path) -> Optional[MetrikOzeti]:
    try:
        m = json.loads((r / "models" / "metrics.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    def gun(h):
        return None if h is None else round(h / 24, 2)

    tipler = {
        t: TipMetrigi(
            ornek_unite=v.get("n_units"),
            saatlik_dogruluk=v.get("accuracy"),
            yakalama_orani=v.get("detection_rate"),
            medyan_erken_uyari_gun=gun(v.get("median_lead_h")),
            yanlis_alarm_unite=v.get("false_alarm_units"),
        )
        for t, v in (m.get("per_tip") or {}).items()
    }
    ortam = {
        kayma: OrtamStresMetrigi(
            ortam_kaymasi_c=v.get("ambient_offset_c"),
            saatlik_dogruluk=v.get("accuracy"),
            makro_f1=v.get("macro_f1"),
            yakalama_orani=v.get("detection_rate"),
            medyan_erken_uyari_saat=v.get("median_lead_h"),
            yanlis_alarm_unite=v.get("false_alarm_units"),
        )
        for kayma, v in (m.get("seasonal_shift") or {}).items()
    }
    return MetrikOzeti(
        egitim_unite_sayisi=m.get("n_train_units"),
        test_unite_sayisi=m.get("n_test_units"),
        gun=m.get("days"),
        saatlik_dogruluk=m.get("accuracy"),
        makro_f1=m.get("macro_f1"),
        yakalama_orani=m.get("detection_rate"),
        medyan_erken_uyari_gun=gun(m.get("median_lead_h")),
        yanlis_alarm_unite=m.get("false_alarm_units"),
        multiclass_brier=(m.get("probability_scores") or {}).get("multiclass_brier"),
        log_loss=(m.get("probability_scores") or {}).get("log_loss"),
        eta_mae_saat=(m.get("eta_error") or {}).get("mae_h"),
        eta_p90_mutlak_hata_saat=(m.get("eta_error") or {}).get("p90_abs_error_h"),
        eta_dogru_tur_mae_saat=(m.get("eta_error") or {}).get("dogru_ariza_turu", {}).get("mae_h"),
        eta_dogru_tur_p90_mutlak_hata_saat=(
            (m.get("eta_error") or {}).get("dogru_ariza_turu", {}).get("p90_abs_error_h")
        ),
        tipe_gore=tipler,
        ortam_kaymasi_stresi=ortam,
    )


# Pydantic hata türleri → Türkçe
_PYDANTIC_TR = {
    "missing": "zorunlu alan eksik",
    "json_invalid": "JSON okunamadı",
    "float_parsing": "sayı bekleniyor",
    "float_type": "sayı bekleniyor",
    "finite_number": "sonlu bir sayı bekleniyor",
    "int_parsing": "tam sayı bekleniyor",
    "bool_parsing": "true/false bekleniyor",
    "bool_type": "true/false bekleniyor",
    "string_type": "metin bekleniyor",
    "datetime_parsing": "tarih-saat okunamadı (ISO 8601, ör. 2026-01-05T14:35:00)",
    "datetime_type": "tarih-saat bekleniyor",
    "list_type": "liste bekleniyor",
    "too_short": "en az bir öğe gerekir",
    "too_long": "izin verilen uzunluk aşıldı",
    "string_too_short": "boş olamaz",
    "string_too_long": "çok uzun",
    "model_attributes_type": "nesne bekleniyor",
    "dict_type": "nesne bekleniyor",
}


def _konum(loc) -> str:
    s = ""
    for p in loc:
        if p in ("body", "query"):
            continue
        s += f"[{p}]" if isinstance(p, int) else (("." if s else "") + str(p))
    return s or "istek"


class _IstekBuyuk(Exception):
    pass


class GovdeBoyutuSiniri:
    """Content-Length olmasa da ASGI gövdesini akış sırasında sınırlayan middleware."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        limit = maks_yukleme_bayt()
        length = next((v for k, v in scope.get("headers", []) if k.lower() == b"content-length"), None)
        if length is not None:
            try:
                if int(length) > limit:
                    return await _hata_yaniti(
                        413, "cok_buyuk", f"İstek en fazla {limit / 1048576:.0f} MB olabilir."
                    )(scope, receive, send)
            except ValueError:
                pass  # Geçersiz başlıkta gerçek gövde boyutu yine sayılır.

        toplam = 0
        yanit_basladi = False

        async def sinirli_al():
            nonlocal toplam
            ileti = await receive()
            if ileti["type"] == "http.request":
                toplam += len(ileti.get("body", b""))
                if toplam > limit:
                    raise _IstekBuyuk
            return ileti

        async def izle_yolla(ileti):
            nonlocal yanit_basladi
            if ileti["type"] == "http.response.start":
                yanit_basladi = True
            await send(ileti)

        try:
            await self.app(scope, sinirli_al, izle_yolla)
        except _IstekBuyuk:
            if not yanit_basladi:
                await _hata_yaniti(
                    413, "cok_buyuk", f"İstek en fazla {limit / 1048576:.0f} MB olabilir."
                )(scope, receive, send)


# ---------------------------------------------------------------------------------------------
# Uygulama
# ---------------------------------------------------------------------------------------------

ACIKLAMA = """
Soğutma ünitelerinin (soğuk oda, dondurucu, market dolabı) sensör verisinden **arıza türünü**,
**sağlık skorunu** ve **arızaya kalan süreyi** tahmin eden servis. Panelin ve `predict.py`'nin
kullandığı analiz akışının aynısını çalıştırır.

* `POST /tahmin/csv`: CSV dosyası yükleyin (biçim: `docs/veri-formati.md`).
* `POST /tahmin/olcumler`: Bir ağ geçidi son ölçümleri JSON olarak gönderebilir.
* `GET /saglik`, `GET /model`: servis ve model durumu.

> **Sentetik veri uyarısı:** Model simülatör verisiyle eğitildi; gerçek saha doğruluğu doğrulanmamıştır.
> Çıktı ön göstergedir, koruma ekipmanı (basınç şalteri, alarm cihazı) yerine geçmez.

Kimlik doğrulama: `SOGUTMA_API_ANAHTARI` ortam değişkeni ayarlıysa `/saglik` dışındaki uçlar `X-API-Anahtari`
başlığı ister.
"""

ETIKETLER = [
    dict(name="Durum", description="Servis ve model bilgisi."),
    dict(name="Tahmin", description="Arıza tahmini."),
]


@asynccontextmanager
async def _omur(app: FastAPI):
    if not os.environ.get("SOGUTMA_API_ANAHTARI"):
        log.warning(
            "SOGUTMA_API_ANAHTARI ayarlı değil: API kimlik doğrulaması olmadan açık. "
            "Ağa açılacaksa bir anahtar belirleyin."
        )
    try:
        app.state.depo.al()
    except ApiHatasi as e:
        log.warning("Model yüklenemedi: %s", e.mesaj)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Soğutma Arıza Tahmini API",
        description=ACIKLAMA,
        version=SURUM,
        openapi_tags=ETIKETLER,
        lifespan=_omur,
    )
    app.state.depo = ModelDeposu()
    app.state.baslangic = time.monotonic()
    app.add_middleware(GovdeBoyutuSiniri)

    @app.exception_handler(ApiHatasi)
    async def _api_hatasi(request: Request, e: ApiHatasi):
        return _hata_yaniti(e.durum, e.kod, e.mesaj, e.ayrintilar)

    @app.exception_handler(RequestValidationError)
    async def _istek_hatasi(request: Request, e: RequestValidationError):
        ayr = [f"{_konum(x['loc'])}: {_PYDANTIC_TR.get(x['type'], x['msg'])}" for x in e.errors()[:50]]
        return _hata_yaniti(
            422, "istek_gecersiz", "İstek geçersiz; ayrıntılar için 'ayrintilar' alanına bakın.", ayr
        )

    @app.exception_handler(Exception)
    async def _beklenmeyen(request: Request, e: Exception):
        log.exception("Beklenmeyen hata: %s", request.url.path)
        return _hata_yaniti(500, "sunucu_hatasi", "Beklenmeyen bir hata oluştu; sunucu günlüğüne bakın.")

    @app.get(
        "/saglik",
        response_model=Saglik,
        responses={503: {"model": Saglik}},
        tags=["Durum"],
        summary="Servis sağlığı (kimlik doğrulama gerekmez)",
    )
    def saglik(request: Request):
        """Servis ayakta mı, model yüklenebiliyor mu? Model kullanılamıyorsa 503 döner
        (Docker / yük dengeleyici denetimi için)."""
        try:
            model = request.app.state.depo.al()
            durum, surum = "ok", getattr(model, "version", None)
        except ApiHatasi as e:
            durum, surum = e.kod, None
        icerik = Saglik(
            durum=durum,
            model_yuklu=durum == "ok",
            model_surumu=surum,
            calisma_suresi_sn=round(time.monotonic() - request.app.state.baslangic, 1),
            kimlik_dogrulama=bool(os.environ.get("SOGUTMA_API_ANAHTARI")),
        )
        return JSONResponse(icerik.model_dump(), status_code=200 if durum == "ok" else 503)

    @app.get(
        "/model",
        response_model=ModelBilgisi,
        responses=HATA_YANITLARI,
        tags=["Durum"],
        dependencies=[Depends(yetki)],
        summary="Model sürümü ve eğitim metrikleri (sentetik)",
    )
    def model_bilgisi(model=Depends(model_al)):
        """Model sürümü ve `models/metrics.json` özeti. Tüm değerler **sentetik** veride ölçülmüştür."""
        yol = ModelDeposu.yol()
        return ModelBilgisi(
            model_surumu=getattr(model, "version", MODEL_VERSION),
            model_dosyasi_zamani=datetime.fromtimestamp(yol.stat().st_mtime),
            ariza_turleri=[ArizaTuru(kod=k, ad=v["ad"]) for k, v in FAULTS.items()],
            ekipman_tipleri=list(TIP_TURLERI),
            metrikler=_metrik_ozeti(kok()),
        )

    @app.post(
        "/tahmin/csv",
        response_model=TahminYaniti,
        response_model_exclude_none=False,
        responses=HATA_YANITLARI,
        tags=["Tahmin"],
        dependencies=[Depends(yetki)],
        summary="CSV dosyasından tahmin",
    )
    def tahmin_csv(
        dosya: UploadFile = File(
            ..., description="Sensör CSV'si (biçim: docs/veri-formati.md). Ünite başına en az 24 saat."
        ),
        setpoint: Optional[float] = Query(
            None, description="CSV'de set sütunu yoksa termostat set değeri (°C)."
        ),
        gauge: bool = Query(False, description="Basınçlar efektif (gauge) ölçülmüşse true."),
        tip: Optional[str] = Query(
            None, description="CSV'de tip sütunu yoksa ekipman tipi: soguk_oda, dondurucu, market_dolabi."
        ),
        saatlik: bool = Query(False, description="true ise saatlik tahmin serisi de döner."),
        model=Depends(model_al),
    ):
        """`predict.py` ile aynı ayrıştırma ve analiz: ünite başına son durum, veri kontrol raporu,
        isteğe bağlı saatlik seri."""
        sinir = maks_yukleme_bayt()
        veri = dosya.file.read(sinir + 1)
        if len(veri) > sinir:
            raise ApiHatasi(
                413, "cok_buyuk", f"Dosya çok büyük; en fazla {sinir / 1048576:.0f} MB kabul edilir."
            )
        return _calistir(
            lambda: analiz_et(io.BytesIO(veri), model, gauge=gauge, setpoint=setpoint, tip=tip),
            model,
            saatlik,
        )

    @app.post(
        "/tahmin/olcumler",
        response_model=TahminYaniti,
        responses=HATA_YANITLARI,
        tags=["Tahmin"],
        dependencies=[Depends(yetki)],
        summary="JSON ölçümlerinden tahmin",
    )
    def tahmin_olcumler(istek: OlcumIstegi, model=Depends(model_al)):
        """Bir ağ geçidinin gönderdiği son ölçümlerden tahmin. Her çağrıda, son durumun hesaplanabilmesi için
        ünite başına son **en az 1 gün** (tercihen 3+ gün) 5 dakikalık ölçüm gönderin; servis durum tutmaz."""
        return _calistir(
            lambda: analiz_et_tablo(
                _tablo(istek.olcumler), model, gauge=istek.gauge, setpoint=istek.setpoint, tip=istek.tip
            ),
            model,
            istek.saatlik,
        )

    return app


app = create_app()
