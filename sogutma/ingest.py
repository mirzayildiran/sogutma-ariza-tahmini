"""Kullanıcının kendi sensör CSV dosyasını simülatörün ham veri biçimine çevirir.

Akış: okuma (ayraç/kodlama/ondalık ayracı tespiti) → sütun adı eşleme → doğrulama
→ ünite başına sıralama, tekilleştirme, 5 dakikalık ızgaraya hizalama → türetilen
sinyaller. Sorunlar Türkçe mesajlarla `ValidationError` olarak (ölümcül) ya da
`IngestReport.uyarilar` içinde (düzeltilebilir) bildirilir.

Eksik isteğe bağlı sensörler NaN sütunu olarak kalır; öznitelik çıkarımı bunları
tolere eder, `FaultPredictor.predict` ise sağlıklı ortalamayla doldurur
(bkz. docs/veri-formati.md).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .simulator import SENSORLER, STEP_MIN, sat_temperature

GAUGE_TO_ABS = 1.013  # bar (efektif) → bar (mutlak)
COMP_ON_THRESHOLD_A = 0.5  # comp_on yoksa: i_comp bu değerin üstündeyse kompresör çalışıyor
MAX_FFILL_MIN = 30  # bu süreye kadar olan boşluklar son değerle doldurulur
MIN_HOURS = 24  # ünite başına en az geçerli veri süresi (saat)
MAX_SAMPLE_MIN = 30  # bundan seyrek örnekleme kabul edilmez
BAD_FRACTION = 0.10  # aralık dışı değer oranı bunu aşarsa sütun hatalı sayılır
MAX_UNITS = 500  # tek analiz isteğinde en çok ünite
MAX_SPAN_DAYS = 366  # tek ünite için en çok analiz aralığı
MAX_GRID_POINTS = 1_000_000  # hizalama sonrası tüm ünitelerde üst sınır; bellek tüketimini sınırlar

# Alan sırası: simülatörün ham veri sütun sırası (etiketler hariç)
SCHEMA = {
    "timestamp": dict(
        zorunlu=True,
        birim="tarih-saat",
        aciklama="Ölçüm zamanı (ör. 05.01.2026 14:35 ya da 2026-01-05 14:35:00)",
        takma=["zaman", "zaman_damgasi", "tarih_saat", "tarih_zaman", "datetime", "date_time", "time"],
    ),
    "unit_id": dict(
        zorunlu=False,
        birim="metin",
        aciklama="Ünite / soğuk oda kimliği (yoksa tek ünite varsayılır)",
        takma=[
            "unit",
            "unit_name",
            "unite",
            "unite_adi",
            "unite_no",
            "oda_adi",
            "oda_no",
            "cihaz",
            "cihaz_adi",
            "cihaz_no",
            "device",
            "device_id",
            "kabin_no",
        ],
    ),
    "t_amb": dict(
        zorunlu=True,
        birim="°C",
        aralik=(-60, 80),
        aciklama="Dış ortam (kondenser havası) sıcaklığı",
        takma=[
            "ambient",
            "ambient_temp",
            "amb_temp",
            "outdoor_temp",
            "dis_sicaklik",
            "dis_ortam",
            "dis_ortam_sicakligi",
            "dis_hava_sicakligi",
            "ortam_sicakligi",
            "ortam",
        ],
    ),
    "t_room": dict(
        zorunlu=True,
        birim="°C",
        aralik=(-60, 80),
        aciklama="Oda (kabin) hava sıcaklığı",
        takma=[
            "room",
            "room_temp",
            "cabin_temp",
            "air_temp",
            "oda_sicakligi",
            "oda_isisi",
            "kabin_sicakligi",
            "donus_havasi",
            "return_air",
        ],
    ),
    "t_coil": dict(
        zorunlu=False,
        birim="°C",
        aralik=(-60, 80),
        aciklama="Evaporatör batarya / defrost sensörü sıcaklığı",
        takma=[
            "coil_temp",
            "evap_temp",
            "evaporator_temp",
            "evaporator_sicakligi",
            "evap_sicakligi",
            "batarya_sicakligi",
            "buharlastirici_sicakligi",
            "defrost_sicakligi",
            "defrost_sensoru",
        ],
    ),
    "p_suc": dict(
        zorunlu=True,
        birim="bar (mutlak)",
        aralik=(0.1, 45),
        aciklama="Emme (alçak) basıncı",
        takma=[
            "suction_pressure",
            "suction",
            "p_low",
            "low_pressure",
            "lp",
            "p_evap",
            "emme_basinci",
            "alcak_basinc",
            "alcak_basinc_hatti",
            "dusuk_basinc",
            "evap_basinci",
        ],
    ),
    "p_dis": dict(
        zorunlu=True,
        birim="bar (mutlak)",
        aralik=(0.1, 45),
        aciklama="Basma (yüksek) basıncı",
        takma=[
            "discharge_pressure",
            "discharge",
            "p_high",
            "high_pressure",
            "hp",
            "p_cond",
            "basma_basinci",
            "yuksek_basinc",
            "yuksek_basinc_hatti",
            "kondenser_basinci",
        ],
    ),
    "sh": dict(
        zorunlu=False,
        birim="K",
        aralik=(-5, 60),
        aciklama="Kızgınlık (superheat); yoksa t_suc ile türetilir",
        takma=["superheat", "super_heat", "kizginlik", "kizgin", "kizdirma", "asiri_isitma"],
    ),
    "sc": dict(
        zorunlu=False,
        birim="K",
        aralik=(-5, 40),
        aciklama="Aşırı soğutma (subcooling); yoksa t_liq ile türetilir",
        takma=["subcooling", "sub_cooling", "asiri_sogutma", "alt_sogutma"],
    ),
    "i_comp": dict(
        zorunlu=True,
        birim="A",
        aralik=(0, 500),
        aciklama="Kompresör akımı",
        takma=[
            "comp_current",
            "compressor_current",
            "kompresor_akimi",
            "kompresor_akim",
            "komp_akimi",
            "akim",
        ],
    ),
    "i_fan": dict(
        zorunlu=False,
        birim="A",
        aralik=(0, 500),
        aciklama="Kondenser fan motoru akımı",
        takma=["fan_current", "fan_akimi", "fan_akim", "kondenser_fan_akimi", "fan_motor_akimi"],
    ),
    "vib": dict(
        zorunlu=False,
        birim="mm/s",
        aralik=(0, 100),
        aciklama="Kompresör titreşimi (RMS hız)",
        takma=["vibration", "titresim", "kompresor_titresimi"],
    ),
    "t_dis": dict(
        zorunlu=False,
        birim="°C",
        aralik=(-60, 150),
        aciklama="Basma hattı sıcaklığı",
        takma=[
            "discharge_temp",
            "discharge_temperature",
            "basma_sicakligi",
            "basma_hatti_sicakligi",
            "bosaltma_sicakligi",
        ],
    ),
    "comp_on": dict(
        zorunlu=False,
        birim="0/1",
        aciklama="Kompresör çalışıyor mu (yoksa i_comp eşiğinden türetilir)",
        takma=[
            "compressor_on",
            "compressor_status",
            "comp_status",
            "compressor",
            "kompresor",
            "kompresor_durumu",
            "komp_durum",
            "kompresor_calisiyor",
            "kompresor_calisma",
        ],
    ),
    "defrost": dict(
        zorunlu=False,
        birim="0/1",
        aciklama="Defrost (buz çözme) aktif mi (yoksa hep 0 varsayılır)",
        takma=["defrost_aktif", "defrost_durumu", "buz_cozme", "buz_cozme_durumu", "cozunme", "eritme"],
    ),
    "door_open": dict(
        zorunlu=False,
        birim="0/1",
        aciklama="Kapı açık mı (modelde kullanılmaz; bilgi amaçlı)",
        takma=["door", "door_status", "kapi", "kapi_acik", "kapi_durumu", "kapi_acikligi"],
    ),
    "setpoint": dict(
        zorunlu=False,
        birim="°C",
        aralik=(-60, 80),
        aciklama="Termostat set değeri (yoksa parametre ya da oda medyanı)",
        takma=[
            "set_point",
            "set",
            "set_degeri",
            "ayar_degeri",
            "ayar_sicakligi",
            "set_sicakligi",
            "hedef_sicaklik",
            "hedef",
            "sp",
        ],
    ),
    "tip": dict(
        zorunlu=False,
        birim="metin",
        aciklama="Ekipman tipi: soguk_oda, dondurucu ya da market_dolabi (yoksa tip parametresi / soguk_oda)",
        takma=["ekipman_tipi", "ekipman", "cihaz_tipi", "unite_tipi", "unit_type", "equipment_type"],
    ),
}
# Ekipman tipi değerleri için kabul edilen yazılışlar (_norm ile normalleştirilmiş)
TIP_ALIASES = {
    "soguk_oda": "soguk_oda",
    "soguk_hava_deposu": "soguk_oda",
    "cold_room": "soguk_oda",
    "coldroom": "soguk_oda",
    "dondurucu": "dondurucu",
    "dondurucu_oda": "dondurucu",
    "derin_dondurucu": "dondurucu",
    "freezer": "dondurucu",
    "market_dolabi": "market_dolabi",
    "market_dolap": "market_dolabi",
    "reyon_dolabi": "market_dolabi",
    "vitrin": "market_dolabi",
    "display": "market_dolabi",
    "display_cabinet": "market_dolabi",
}
# Yalnızca sh / sc türetmek için okunan, çıktıya girmeyen yardımcı sensörler
EXTRA_SCHEMA = {
    "t_suc": dict(
        birim="°C",
        aralik=(-60, 80),
        aciklama="Emme hattı sıcaklığı (sh türetmek için)",
        takma=["suction_temp", "suction_line_temp", "emme_hatti_sicakligi", "emme_sicakligi"],
    ),
    "t_liq": dict(
        birim="°C",
        aralik=(-60, 80),
        aciklama="Sıvı hattı sıcaklığı (sc türetmek için)",
        takma=["liquid_temp", "liquid_line_temp", "sivi_hatti_sicakligi", "sivi_sicakligi"],
    ),
}
REQUIRED = [c for c, s in SCHEMA.items() if s["zorunlu"]]
OPTIONAL = [c for c, s in SCHEMA.items() if not s["zorunlu"]]
OUT_COLUMNS = list(SCHEMA)
FLAG_COLS = ["comp_on", "defrost", "door_open"]
NUM_COLS = [c for c in OUT_COLUMNS if c not in ("timestamp", "unit_id", "tip", *FLAG_COLS)]
# Tarih ve saat ayrı sütunlardaysa (logger dışa aktarımlarında yaygın)
_DATE_ALIASES, _TIME_ALIASES = ["tarih", "date", "gun"], ["saat", "hour"]

_TR = str.maketrans("İIıŞşÇçĞğÖöÜü", "iiiSsCcGgOoUu")
_TRUE = {"1", "true", "evet", "acik", "on", "var", "aktif", "yes", "y", "e", "dogru", "calisiyor"}
_FALSE = {"0", "false", "hayir", "kapali", "off", "yok", "pasif", "no", "n", "h", "yanlis", "durdu"}
_UNIT_TOKENS = ("_c", "_bar", "_a", "_amp", "_mm_s", "_mms", "_k", "_kpa", "_01")


class ValidationError(ValueError):
    """Veri dosyası kullanılamaz durumda; `hatalar` Türkçe mesaj listesidir."""

    def __init__(self, hatalar):
        self.hatalar = [hatalar] if isinstance(hatalar, str) else list(hatalar)
        super().__init__("\n".join("• " + h for h in self.hatalar))


@dataclass
class IngestReport:
    uyarilar: List[str] = field(default_factory=list)
    bilgiler: List[str] = field(default_factory=list)
    eksik_sensorler: List[str] = field(default_factory=list)
    turetilen: Dict[str, str] = field(default_factory=dict)
    uniteler: Dict[str, dict] = field(default_factory=dict)
    kopya_satir: int = 0

    def ozet(self):
        satirlar = list(self.bilgiler)
        if self.turetilen:
            satirlar.append(
                "Türetilen / varsayılan alanlar: "
                + "; ".join(f"{k} ← {v}" for k, v in self.turetilen.items())
            )
        satirlar += ["UYARI: " + u for u in self.uyarilar]
        return "\n".join(satirlar)


def _norm(name):
    """Sütun adını eşleştirme anahtarına çevirir: 'Oda Sıcaklığı (°C)' → 'oda_sicakligi'."""
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", str(name)).translate(_TR).lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def _alias_map():
    m = {}
    for table in (SCHEMA, EXTRA_SCHEMA):
        for canon, spec in table.items():
            for a in [canon] + spec["takma"]:
                m[_norm(a)] = canon
    for a in _DATE_ALIASES:
        m[_norm(a)] = "_tarih"
    for a in _TIME_ALIASES:
        m[_norm(a)] = "_saat"
    return m


_ALIASES = _alias_map()


def _canon_tip(value):
    """Ekipman tipi yazılışını kanonik anahtara çevirir; tanınmazsa None."""
    return TIP_ALIASES.get(_norm(value))


def _lookup(col):
    k = _norm(col)
    if k in _ALIASES:
        return _ALIASES[k]
    for tok in _UNIT_TOKENS:  # 'oda_sicakligi_c' gibi birim son ekleri
        if k.endswith(tok) and k[: -len(tok)] in _ALIASES:
            return _ALIASES[k[: -len(tok)]]
    return None


def _read_text(source):
    if isinstance(source, (str, Path)):
        if not Path(source).is_file():
            raise ValidationError(f"Dosya bulunamadı: {source}")
        raw = Path(source).read_bytes()
    else:
        raw = source.read()
    if isinstance(raw, str):
        return raw
    for enc in ("utf-8-sig", "cp1254"):  # cp1254: Türkçe Windows / Excel
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _detect_sep(text):
    head = next((ln for ln in text.splitlines() if ln.strip()), "")
    counts = {s: head.count(s) for s in (";", "\t", ",")}
    best = max(counts, key=lambda s: (counts[s], s == ";"))
    return best if counts[best] else ";"


def _to_num(s):
    """Metni sayıya çevirir; '12,5' ve '1.234,5' gibi Türkçe biçimleri de anlar."""
    s = s.astype(str).str.strip()
    both = s.str.contains(",", regex=False) & s.str.contains(".", regex=False)
    s = s.where(~both, s.str.replace(".", "", regex=False))
    s = s.str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")


def _to_flag(s):
    key = s.astype(str).str.strip().str.translate(_TR).str.lower()
    out = pd.Series(np.nan, index=s.index)
    out[key.isin(_TRUE)] = 1.0
    out[key.isin(_FALSE)] = 0.0
    rest = out.isna() & (key != "")
    if rest.any():  # '1,0' / '0.0' gibi sayısal gösterimler
        num = _to_num(s[rest])
        out[rest] = (num > 0).astype(float).where(num.notna())
    return out


_TS_FORMATS = [
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%Y-%m-%d",
]


def _parse_time(s):
    s = s.astype(str).str.strip()
    best = None
    for fmt in _TS_FORMATS:  # hızlı yol: tek biçimle %99+ çözülüyorsa onu kullan
        t = pd.to_datetime(s, format=fmt, errors="coerce")
        if best is None or t.notna().sum() > best.notna().sum():
            best = t
        if t.notna().mean() >= 0.99:
            return t
    try:  # yedek: karışık biçim
        # Naif damgalar UTC kabul edilir; açık offset taşıyanlar UTC'ye çevrilir.
        t = pd.to_datetime(s, errors="coerce", dayfirst=True, format="mixed", utc=True).dt.tz_localize(None)
    except (ValueError, TypeError, AttributeError):
        return best
    return t if t.notna().sum() >= best.notna().sum() else best


def _check_range(vals, col, report, errors, *, allow_sensor_faults=False):
    lo, hi = (SCHEMA.get(col) or EXTRA_SCHEMA[col])["aralik"]
    bad = vals.notna() & ((vals < lo) | (vals > hi))
    n_ok = int(vals.notna().sum())
    if not bad.any():
        return vals
    frac = bad.sum() / max(n_ok, 1)
    birim = (SCHEMA.get(col) or EXTRA_SCHEMA[col])["birim"]
    if allow_sensor_faults:
        report.uyarilar.append(
            f"'{col}': {int(bad.sum())} değer fiziksel aralığın ({lo}..{hi} {birim}) dışında; "
            "sensör sağlığı denetimine bırakıldı."
        )
        return vals
    if frac > BAD_FRACTION:
        errors.append(
            f"'{col}' değerlerinin {frac:.0%} kadarı fiziksel aralığın ({lo}..{hi} {birim}) dışında "
            f"(örnek: {vals[bad].iloc[0]:g}). Birim, sütun eşlemesi ya da ondalık ayracı yanlış olabilir."
            + (" Basınçlar efektif (gauge) ise --gauge kullanın." if col.startswith("p_") else "")
        )
    else:
        report.uyarilar.append(
            f"'{col}': {int(bad.sum())} değer fiziksel aralığın ({lo}..{hi} {birim}) dışında; eksik sayıldı."
        )
        vals = vals.where(~bad)
    return vals


def _prepare_unit(g, uid, report, *, comp_threshold, max_gap_min, setpoint, derive_sh, derive_sc, errors):
    """Tek ünite: sırala, tekilleştir, 5 dk ızgaraya hizala, kısa boşlukları doldur."""
    g = g.sort_values("timestamp")
    dup = int(g.duplicated("timestamp", keep="last").sum())
    if dup:
        report.kopya_satir += dup
        g = g.drop_duplicates("timestamp", keep="last")
    steps = g["timestamp"].diff().dropna()
    native_min = steps.median() / pd.Timedelta(minutes=1) if len(steps) else float("nan")
    if not native_min == native_min:  # NaN
        errors.append(
            f"Ünite '{uid}': tek bir ölçüm var, analiz için en az {MIN_HOURS} saatlik veri gerekir."
        )
        return None
    if native_min > MAX_SAMPLE_MIN:
        errors.append(
            f"Ünite '{uid}': örnekleme aralığı ~{native_min:.0f} dk; en fazla {MAX_SAMPLE_MIN} dk "
            "(tercihen 1–5 dk) olmalı."
        )
        return None
    if native_min > STEP_MIN * 1.5:
        report.uyarilar.append(
            f"Ünite '{uid}': örnekleme ~{native_min:.0f} dk (önerilen ≤ {STEP_MIN} dk); "
            "kompresör kalkış sayısı gibi sinyaller doğru ölçülemeyebilir."
        )

    g = g.set_index("timestamp")
    r = g[NUM_COLS + ["comp_on", "defrost", "t_suc", "t_liq"]].resample(f"{STEP_MIN}min")
    d = r.mean()
    d["door_open"] = g["door_open"].resample(f"{STEP_MIN}min").max()
    limit = max(1, int(round(max(max_gap_min, native_min) / STEP_MIN)))
    d = d.ffill(limit=limit)

    # Çok uzun boşluklar NaN kalır; kapsam oranını raporla
    ok = d[["t_amb", "t_room", "p_suc", "p_dis", "i_comp"]].notna().all(axis=1)
    hours = ok.sum() * STEP_MIN / 60
    report.uniteler[uid] = dict(
        bas=d.index[0],
        bit=d.index[-1],
        saat=float(hours),
        kapsam=float(ok.mean()),
        ham_satir=len(g),
        orneklem_dk=float(native_min),
    )
    if hours < MIN_HOURS:
        errors.append(
            f"Ünite '{uid}': geçerli veri yalnızca {hours:.1f} saat; en az {MIN_HOURS} saat (1 gün) "
            "gerekir, güvenilir sonuç için 3+ gün önerilir."
        )
        return None
    if ok.mean() < 0.8:
        report.uyarilar.append(
            f"Ünite '{uid}': verinin yalnızca {ok.mean():.0%} kadarı eksiksiz; uzun boşluklar yoksayılır."
        )

    # comp_on: yoksa (ya da boşsa) i_comp eşiğinden türet
    derived = d["i_comp"] > comp_threshold
    on = d["comp_on"] >= 0.5
    d["comp_on"] = on.where(d["comp_on"].notna(), derived).astype(bool)
    d["defrost"] = (d["defrost"] >= 0.5).astype(bool)
    d["door_open"] = (d["door_open"] > 0).astype(bool)
    if not d["comp_on"].any():
        report.uyarilar.append(f"Ünite '{uid}': kompresör hiç çalışmıyor görünüyor; tahmin yapılamaz.")

    # Yardımcı sensörlerden kızgınlık / aşırı soğutma
    if derive_sh:
        d["sh"] = d["t_suc"] - sat_temperature(d["p_suc"])
    if derive_sc:
        d["sc"] = sat_temperature(d["p_dis"]) - d["t_liq"]

    # Set değeri: sütun > parametre > oda sıcaklığı medyanı
    if d["setpoint"].isna().all():
        d["setpoint"] = setpoint if setpoint is not None else d["t_room"].median()
    else:
        d["setpoint"] = d["setpoint"].ffill().bfill()
    return d.drop(columns=["t_suc", "t_liq"])


def load_dataframe(
    df,
    *,
    gauge=False,
    setpoint=None,
    unit_id=None,
    tip=None,
    comp_threshold=COMP_ON_THRESHOLD_A,
    max_gap_min=MAX_FFILL_MIN,
    allow_sensor_faults=False,
):
    """Metin sütunlu ham tabloyu doğrular ve simülatör ham biçimine çevirir.

    `tip`: CSV'de `tip` sütunu yoksa tüm üniteler için ekipman tipi (soguk_oda, dondurucu,
    market_dolabi); verilmezse soguk_oda.
    """
    report, errors = IngestReport(), []
    default_tip = "soguk_oda"
    if tip is not None:
        default_tip = _canon_tip(tip)
        if default_tip is None:
            raise ValidationError(
                f"Bilinmeyen ekipman tipi: '{tip}'. Geçerli değerler: soguk_oda, dondurucu, market_dolabi."
            )

    # 1) Sütun eşleme
    rename, seen, ignored = {}, {}, []
    for c in df.columns:
        canon = _lookup(c)
        if canon is None:
            ignored.append(str(c))
        elif canon in seen:
            report.uyarilar.append(
                f"'{c}' sütunu '{seen[canon]}' ile aynı alana ({canon}) eşleşiyor; yoksayıldı."
            )
        else:
            seen[canon] = str(c)
            rename[c] = canon
    d = df[list(rename)].rename(columns=rename)
    if ignored:
        report.bilgiler.append("Tanınmayan sütunlar yoksayıldı: " + ", ".join(ignored))

    if "timestamp" not in d and "_tarih" in d:
        stamp = d["_tarih"].astype(str).str.strip()
        if "_saat" in d:
            stamp = stamp + " " + d["_saat"].astype(str).str.strip()
        d["timestamp"] = stamp
    missing = [c for c in REQUIRED if c not in d]
    if missing:
        ornek = {c: ", ".join([c] + SCHEMA[c]["takma"][:3]) for c in missing}
        errors.append(
            "Zorunlu sütun(lar) eksik: "
            + "; ".join(
                f"{c} ({SCHEMA[c]['aciklama'].split(' (')[0]}) — kabul edilen adlar: {ornek[c]}"
                for c in missing
            )
        )
        raise ValidationError(errors)

    # 2) Zaman damgası
    d = d.copy()
    ts = _parse_time(d["timestamp"])
    bad_ts = ts.isna()
    if bad_ts.mean() > 0.01:
        ex = d["timestamp"][bad_ts].iloc[0]
        raise ValidationError(
            f"Zaman damgası okunamadı: {int(bad_ts.sum())} / {len(d)} satır "
            f"(örnek: '{ex}'). Desteklenen biçimler: 05.01.2026 14:35, 2026-01-05 14:35:00."
        )
    if bad_ts.any():
        report.uyarilar.append(f"{int(bad_ts.sum())} satırın zaman damgası okunamadı; satırlar atıldı.")
    d["timestamp"] = ts
    d = d[~bad_ts]

    # 3) Ünite kimliği
    if "unit_id" in d:
        d["unit_id"] = d["unit_id"].astype(str).str.strip().replace("", "U1")
    else:
        d["unit_id"] = unit_id or "U1"
        report.bilgiler.append(f"unit_id sütunu yok; tek ünite varsayıldı ('{d['unit_id'].iloc[0]}').")

    uniteler = d.groupby("unit_id", sort=False)["timestamp"]
    if d["unit_id"].nunique() > MAX_UNITS:
        raise ValidationError(f"Tek analiz isteğinde en fazla {MAX_UNITS} ünite desteklenir.")
    grid_nokta = 0
    for uid, zamanlar in uniteler:
        span = zamanlar.max() - zamanlar.min()
        if span > pd.Timedelta(days=MAX_SPAN_DAYS):
            raise ValidationError(
                f"Ünite '{uid}': analiz aralığı en fazla {MAX_SPAN_DAYS} gün olabilir."
            )
        grid_nokta += int(np.ceil(span / pd.Timedelta(minutes=STEP_MIN))) + 13
    if grid_nokta > MAX_GRID_POINTS:
        raise ValidationError(
            f"Veri 5 dakikalık ızgaraya hizalandığında çok büyük ({grid_nokta:,} nokta); "
            f"üst sınır {MAX_GRID_POINTS:,}. Ünite veya tarih aralığını azaltın."
        )

    # 3b) Ekipman tipi: sütun > tip parametresi > soğuk oda; ünite başına tek değer (en sık görülen)
    if "tip" in d:
        canon = d["tip"].astype(str).str.strip().map(_canon_tip)
        unknown = d["tip"][canon.isna() & (d["tip"].astype(str).str.strip() != "")]
        if len(unknown):
            raise ValidationError(
                f"'tip' sütununda tanınmayan ekipman tipi: '{unknown.iloc[0]}'. "
                "Geçerli değerler: soguk_oda, dondurucu, market_dolabi."
            )
        d["tip"] = canon.fillna(default_tip)
        for uid_, g_ in d.groupby("unit_id"):
            if g_["tip"].nunique() > 1:
                report.uyarilar.append(
                    f"Ünite '{uid_}': birden çok ekipman tipi var; en sık görülen kullanıldı."
                )
        d["tip"] = d.groupby("unit_id")["tip"].transform(lambda s: s.mode().iloc[0])
    else:
        d["tip"] = default_tip

    # 4) Sayısal / mantıksal sütunlar
    for c in NUM_COLS + list(EXTRA_SCHEMA):
        if c not in d:
            d[c] = np.nan
            continue
        raw = d[c].astype(str).str.strip()
        vals = _to_num(raw)
        filled = raw != ""
        unread = (
            filled
            & vals.isna()
            & ~raw.str.lower().isin(["nan", "null", "none", "n/a", "na", "-", "--", "---"])
        )
        if filled.any() and vals.notna().sum() < 0.5 * filled.sum():
            if c in REQUIRED:
                ornek = raw[unread].iloc[0] if unread.any() else raw[filled].iloc[0]
                errors.append(f"'{c}' sütunu sayısal okunamadı (örnek değer: '{ornek}').")
            else:
                report.uyarilar.append(f"İsteğe bağlı '{c}' sütunu sayısal okunamadı; yok sayıldı.")
            vals = pd.Series(np.nan, index=d.index)
        elif unread.sum() > 0.01 * len(d):
            report.uyarilar.append(
                f"'{c}': {int(unread.sum())} değer sayıya çevrilemedi (örnek: "
                f"'{raw[unread].iloc[0]}'); eksik sayıldı."
            )
        d[c] = vals
    for c in FLAG_COLS:
        d[c] = _to_flag(d[c]) if c in d else np.nan
    if errors:
        raise ValidationError(errors)

    # 5) Basınç birimi ve fiziksel aralık kontrolleri
    if gauge:
        d["p_suc"] += GAUGE_TO_ABS
        d["p_dis"] += GAUGE_TO_ABS
        report.bilgiler.append(
            f"Basınçlar efektif (gauge) kabul edildi; +{GAUGE_TO_ABS} bar ile mutlağa çevrildi."
        )
    elif d["p_suc"].notna().any() and (d["p_suc"] < 1.0).mean() > 0.2:
        report.uyarilar.append(
            "Emme basıncının %20'sinden fazlası 1 bar'ın altında; basınçlar efektif (gauge) "
            "ölçülüyorsa --gauge kullanın."
        )
    for c in NUM_COLS + list(EXTRA_SCHEMA):
        if d[c].notna().any():
            d[c] = _check_range(
                d[c], c, report, errors,
                allow_sensor_faults=allow_sensor_faults and c in SENSORLER,
            )
    if errors:
        raise ValidationError(errors)

    comp = (d["comp_on"] >= 0.5).where(d["comp_on"].notna(), d["i_comp"] > comp_threshold)
    running = comp & (d["defrost"].fillna(0) < 0.5)
    inv = running & d["p_suc"].notna() & d["p_dis"].notna() & (d["p_suc"] >= d["p_dis"])
    n_run = int((running & d["p_suc"].notna() & d["p_dis"].notna()).sum())
    if n_run and inv.sum() / n_run > BAD_FRACTION and not allow_sensor_faults:
        raise ValidationError(
            "Kompresör çalışırken emme basıncı basma basıncından büyük ya da eşit: "
            f"{int(inv.sum())} / {n_run} "
            "satır ({:.0%}). p_suc ile p_dis sütunları yer değiştirmiş ya da farklı birimde olabilir.".format(
                inv.sum() / n_run
            )
        )
    if inv.any() and not allow_sensor_faults:
        report.uyarilar.append(
            f"{int(inv.sum())} satırda kompresör çalışırken emme ≥ basma basıncı; "
            "bu satırların basınç değerleri eksik sayıldı."
        )
        d.loc[inv, ["p_suc", "p_dis"]] = np.nan

    # 6) Ünite başına hizalama
    derive_sh = d["sh"].isna().all() and d["t_suc"].notna().any()
    derive_sc = d["sc"].isna().all() and d["t_liq"].notna().any()
    parts = []
    unit_tip = d.groupby("unit_id")["tip"].first()
    for uid, g in d.groupby("unit_id", sort=False):
        u = _prepare_unit(
            g,
            uid,
            report,
            comp_threshold=comp_threshold,
            max_gap_min=max_gap_min,
            setpoint=setpoint,
            derive_sh=derive_sh,
            derive_sc=derive_sc,
            errors=errors,
        )
        if u is not None:
            parts.append(u.rename_axis("timestamp").reset_index().assign(unit_id=uid, tip=unit_tip[uid]))
    if not parts:
        raise ValidationError(errors)
    for e in errors:  # kullanılabilir ünite varsa kısa/bozuk üniteler yalnızca uyarıdır
        report.uyarilar.append(e + " Bu ünite atlandı.")

    out = pd.concat(parts, ignore_index=True)[OUT_COLUMNS]
    if report.kopya_satir:
        report.uyarilar.append(
            f"{report.kopya_satir} yinelenen zaman damgası satırı silindi (sonuncusu tutuldu)."
        )
    report.eksik_sensorler = [
        c for c in OPTIONAL if c not in FLAG_COLS + ["setpoint", "unit_id", "tip"] and out[c].isna().all()
    ]
    if derive_sh:
        report.turetilen["sh"] = "t_suc ve emme basıncındaki doyma sıcaklığından"
    if derive_sc:
        report.turetilen["sc"] = "basma basıncındaki doyma sıcaklığı ve t_liq'den"
    if "comp_on" not in seen:
        report.turetilen["comp_on"] = f"i_comp > {comp_threshold} A eşiğinden"
    if "defrost" not in seen:
        report.turetilen["defrost"] = "sütun yok; hep 0 (defrost yok) varsayıldı"
    if "setpoint" not in seen:
        report.turetilen["setpoint"] = (
            "--setpoint değeri" if setpoint is not None else "oda sıcaklığı medyanı (set değeri verilmedi)"
        )
    if "tip" not in seen:
        report.turetilen["tip"] = (
            "tip parametresi" if tip is not None else "sütun yok; soğuk oda (soguk_oda) varsayıldı"
        )
        if tip is None:
            cold = [
                str(u) for u, g in out.groupby("unit_id") if g["t_room"].median() < -10  # dondurucu aralığı
            ]
            if cold:
                report.uyarilar.append(
                    f"Ünite(ler) {', '.join(cold)}: oda sıcaklığı −10 °C altında ama ekipman tipi verilmedi "
                    "(soğuk oda varsayıldı). Dondurucu ise tip='dondurucu' (CLI: --tip dondurucu) verin."
                )
    gercek = [c for c in report.eksik_sensorler if c not in report.turetilen]
    if gercek:
        report.bilgiler.append(
            "Eksik isteğe bağlı sensörler (ilgili sinyaller nötr sayılır, doğruluk düşebilir): "
            + ", ".join(gercek)
        )
    out.attrs["rapor"] = report
    return out


def load_csv(source, *, sep: Optional[str] = None, **kwargs):
    """CSV dosyasını (yol ya da dosya benzeri nesne) okuyup doğrulanmış ham DataFrame döndürür.

    Anahtar sözcükler: gauge, setpoint, unit_id, comp_threshold, max_gap_min.
    Doğrulama raporu: `df.attrs["rapor"]` (IngestReport). Kullanılamaz veride ValidationError.
    """
    text = _read_text(source)
    if not text.strip():
        raise ValidationError("Dosya boş.")
    try:
        df = pd.read_csv(
            io.StringIO(text),
            sep=sep or _detect_sep(text),
            dtype=str,
            keep_default_na=False,
            skipinitialspace=True,
        )
    except Exception as e:  # pandas ayrıştırma hataları çeşitli türlerde gelir
        raise ValidationError(f"CSV okunamadı: {e}")
    if len(df) == 0:
        raise ValidationError("Dosyada veri satırı yok (yalnızca başlık satırı var).")
    return load_dataframe(df, **kwargs)
