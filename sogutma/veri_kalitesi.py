"""Sensör sağlığı kontrolleri: bozuk sensör ile bozuk ekipmanı ayırmak için veri kalitesi katmanı.

Sahada yanlış alarmların büyük kısmı soğutma ekipmanından değil, takılan, kayan ya da kopan
sensörlerden gelir. Bu modül ünite başına 5 dakikalık ham veriyi tarar ve her sensör için saatlik
bir durum kodu üretir (bkz. `NEDEN_ADI`):

  takili        değer değişmiyor (donmuş okuma)
  kopuk         sabit ve anlamsız değer (ör. −50 °C, 0 bar, çalışırken 0 A)
  veri_kaybi    saatin yarısından fazlası eksik
  tutarsiz      sensörler arası fiziksel ilişki bozuk (kayma ya da yanlış kalibrasyon şüphesi)
  gurultu       aşırı gürültü dönemi
  ani_sicrama   tek örneklik sıçramalar (örnekler silinir)
  aralik_disi   fiziksel olamayacak tek tük değerler (örnekler silinir)

Aşağıdakiler yalnızca simülatör verisinde ayarlanmış sezgisel kurallardır (eşikler fiziksel
mantık + sağlıklı simülasyondaki dağılım): gerçek sahada yeniden kalibre edilmeleri gerekir.
Kayma, tek başına sensörden anlaşılamaz; yalnızca başka sensörlerle olan fiziksel ilişkiden
(`_iliskiler`) yakalanır, bu yüzden bu ilişkisi olmayan sensörlerde (sh, sc, i_fan...) kayma
görülmez. Ekipman etiketi ya da model çıktısı kullanılmaz; gerçek arızaların gizlenmesi
yalnızca ilgili sensörün özniteliklerinin nötrlenmesiyle sınırlıdır.

Kullanım:
    kalite = degerlendir(raw)                  # raw: simülatör / ingest ham veri tablosu
    H = hourly_features(kalite.temiz)          # ani sıçrama / aralık dışı örnekler silinmiş veri
    H, notlar = kalite.uygula(H)               # bozuk sensörlerin öznitelikleri NaN (nötr) yapılır
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np
import pandas as pd

from .features import FEATURE_LABELS, WINDOW_H
from .simulator import SENSORLER, STEP_MIN, sat_temperature

PER_H = 60 // STEP_MIN

NEDEN_ADI = {
    "takili": "takılı (değer değişmiyor)",
    "kopuk": "kopuk / anlamsız değer okuyor",
    "veri_kaybi": "veri kaybı var",
    "tutarsiz": "kaymış ya da diğer sensörlerle tutarsız",
    "gurultu": "aşırı gürültülü",
    "ani_sicrama": "ani sıçramalar yapıyor (sıçrayan örnekler silindi)",
    "aralik_disi": "fiziksel aralık dışı değerler okudu (örnekler silindi)",
}
# Öncelik sırası (bir saat için tek kod tutulur); ilk 5'i özniteliği nötrler, son 2'si (ani_sicrama,
# aralik_disi) yalnızca ilgili örnekleri siler
NEDENLER = ["kopuk", "takili", "veri_kaybi", "tutarsiz", "gurultu", "ani_sicrama", "aralik_disi"]
NOTRLEYEN = {"kopuk", "takili", "veri_kaybi", "tutarsiz", "gurultu"}

SENSOR_ADI = {
    "t_amb": "Dış ortam sıcaklığı", "t_room": "Oda sıcaklığı", "t_coil": "Batarya sıcaklığı",
    "p_suc": "Emme basıncı", "p_dis": "Basma basıncı", "sh": "Kızgınlık (superheat)",
    "sc": "Aşırı soğutma (subcooling)", "i_comp": "Kompresör akımı", "i_fan": "Fan akımı",
    "vib": "Kompresör titreşimi", "t_dis": "Basma hattı sıcaklığı",
}
# Bir sensör bozulursa güvenilmez olan model öznitelikleri
SENSOR_OZNITELIK = {
    "t_amb": ["t_amb", "cond_approach"], "t_room": ["t_room_dev", "evap_delta"],
    "t_coil": ["coil_defrost_peak"], "p_suc": ["p_suc", "evap_delta"],
    "p_dis": ["p_dis", "cond_approach"], "sh": ["sh"], "sc": ["sc"], "i_comp": ["i_comp"],
    "i_fan": ["i_fan"], "vib": ["vib"], "t_dis": ["t_dis"],
}
assert all(f in FEATURE_LABELS for fs in SENSOR_OZNITELIK.values() for f in fs)

# Makul çalışma aralığı (ingest'in fiziksel sınırlarından dar): bunun dışı sensör hatasıdır
OLASI_ARALIK = {
    "t_amb": (-35, 60), "t_room": (-40, 45), "t_coil": (-45, 70), "p_suc": (0.3, 25), "p_dis": (1.0, 40),
    "sh": (-3, 60), "sc": (-3, 40), "i_comp": (0, 200), "i_fan": (0, 100), "vib": (0, 80),
    "t_dis": (-30, 150),
}
# Yalnızca kompresör çalışırken anlamlı sensörler (dururken değer NaN ya da 0 olabilir)
KOMP_SENSORLERI = ["sh", "sc", "i_comp", "i_fan", "vib"]
# Takılı sayılma: bu kadar ardışık örnekte (maks − min) bu değeri aşmıyorsa
TAKILI_ORNEK = {s: (12 if s in KOMP_SENSORLERI else 24) for s in SENSORLER}
TAKILI_TOL = {"t_amb": 0.02, "t_room": 0.02, "t_coil": 0.02, "p_suc": 0.004, "p_dis": 0.004, "sh": 0.005,
              "sc": 0.005, "i_comp": 0.005, "i_fan": 0.002, "vib": 0.002, "t_dis": 0.02}
# Ani sıçrama: yerel medyandan sapma hem bu mutlak değeri hem birim gürültüsünün 8 katını aşmalı
SICRAMA_MIN = {"t_amb": 2.0, "t_room": 2.0, "t_coil": 3.0, "p_suc": 0.4, "p_dis": 1.0, "sh": 4.0, "sc": 3.0,
               "i_comp": 2.0, "i_fan": 0.8, "vib": 1.5, "t_dis": 6.0}
SICRAMA_K = 8.0
GURULTU_K = 3.5  # saatlik medyan |artık| bu çarpanla birim gürültüsünün üstündeyse gürültülü
# Birim gürültüsü tahmininin alt sınırı (sağlıklı simülasyonda sensör başına tipik |artık| üst değeri):
GURULTU_TABAN = {"t_amb": 0.45, "t_room": 0.08, "t_coil": 0.3, "p_suc": 0.1, "p_dis": 0.2, "sh": 0.6,
                 "sc": 0.5, "i_comp": 0.3, "i_fan": 0.06, "vib": 0.25, "t_dis": 1.0}
EKSIK_ORAN = 0.5  # saatlik eksik örnek oranı bundan büyükse veri kaybı
ARALIK_DISI_ORAN = 0.5  # saatlik aralık dışı oran bundan büyükse kopuk
ILISKI_PENCERE_H = 12  # ilişki sapmasının medyanı için pencere
ILISKI_MIN_ORNEK = 12
ILISKI_TOL_K = 1.0  # ilişki sapması (K) bunu aşarsa tutarsız
# Sağlıklı simülasyondaki merkez değerler (K): bkz. `_iliskiler`
MU_CALISIRKEN_BATARYA = 1.05  # t_coil − Tdoyma(p_suc), kompresör çalışırken
MU_DURUNCA_EMME_ODA = -0.25  # Tdoyma(p_suc) − t_room, uzun duruşta
MU_DURUNCA_BATARYA_ODA = -0.2  # t_coil − t_room, uzun duruşta
MU_DURUNCA_BASMA_DIS = 2.0  # Tdoyma(p_dis) − t_amb, uzun duruşta
MU_DURUNCA_BASMA_HAT = 0.0  # t_dis − t_amb, uzun duruşta
UZUN_DURUS = 12  # örnek (1 saat)
SIFIR_REF_TOL = {"i_comp": 0.4, "i_fan": 0.25, "vib": 0.4}  # kompresör dururken beklenen ≈ 0
EVAP_ODA_MIN_K = 3.0  # çalışırken oda − Tdoyma(emme) bunun altına inemez (sağlıklıda ≥ 7)
YOGUSMA_MIN_K = 3.0  # çalışırken Tdoyma(basma) − dış sıcaklık bunun altına inemez (sağlıklıda ≥ 7)


@dataclass
class KaliteRaporu:
    """`degerlendir` çıktısı. `saatlik`: (unit_id, saat) × sensör tablosu; hücre = neden kodu ya da ''."""

    saatlik: pd.DataFrame
    temiz: pd.DataFrame  # ham veri; ani sıçrama, aralık dışı ve takılı örnekler NaN yapılmış
    eksik_orani: Dict[Tuple[str, str], float]  # (ünite, sensör) → tüm veride eksik oranı

    def sorunlar(self) -> pd.DataFrame:
        """Ünite × sensör × neden özeti: ilk/son görülme, etkilenen saat sayısı."""
        s = self.saatlik.stack()
        s = s[s != ""].rename("neden").reset_index()
        s.columns = ["unit_id", "timestamp", "sensor", "neden"]
        if s.empty:
            return pd.DataFrame(columns=["unit_id", "sensor", "neden", "ilk", "son", "saat"])
        g = s.groupby(["unit_id", "sensor", "neden"], sort=False)["timestamp"]
        out = g.agg(ilk="min", son="max", saat="count").reset_index()
        out["oncelik"] = out["neden"].map(NEDENLER.index)
        out = out.sort_values(["unit_id", "sensor", "oncelik"])
        return out.drop(columns="oncelik").reset_index(drop=True)

    def ozet(self) -> str:
        """İnsan okuyabilir kısa özet (predict.py için); sorun yoksa boş metin."""
        s = self.sorunlar()
        if s.empty:
            return ""
        satirlar = []
        for r in s.itertuples():
            satirlar.append(f"Ünite {r.unit_id}: {SENSOR_ADI[r.sensor]} {NEDEN_ADI[r.neden]} "
                            f"({r.saat} saat, ilk: {r.ilk:%d.%m.%Y %H:%M})")
        return "\n".join(satirlar)

    def pencere_kodlari(self) -> pd.DataFrame:
        """Her (ünite, saat) için son `WINDOW_H` saatteki en son nötrleyen kod (yoksa '')."""
        parcalar = []
        for uid, g in self.saatlik.groupby(level=0, sort=False):
            g = g.droplevel(0)
            g = g.reindex(pd.date_range(g.index.min(), g.index.max(), freq="h"), fill_value="")
            sira = np.arange(len(g))
            kod = g.copy()
            for s in g.columns:
                arr = g[s].to_numpy(dtype=object)
                son = np.maximum.accumulate(np.where(g[s].isin(NOTRLEYEN).to_numpy(), sira, -1))
                gecerli = (son >= 0) & (sira - son < WINDOW_H)
                kod[s] = np.where(gecerli, arr[np.maximum(son, 0)], "")
            kod.index = pd.MultiIndex.from_arrays([[uid] * len(kod), kod.index])
            parcalar.append(kod)
        return pd.concat(parcalar)

    def uygula(self, H: pd.DataFrame):
        """Bozuk sensörlerin özniteliklerini NaN (nötr) yapar.

        Döndürür: (nötrlenmiş H kopyası, notlar). `notlar` H ile aynı indeksli bir DataFrame:
        `sensor_sorunu` (bool: son 12 saatte öznitelik nötrlenen sensör var) ve `sensor_notu` (metin).
        Öznitelikler 12 saatlik kayan ortalama olduğundan, bir sensör son `WINDOW_H` saatte nötrleyen
        bir nedenle işaretlendiyse ilgili öznitelikler güvenilmez sayılır. Model NaN'ı tip referansıyla
        doldurur (`FaultPredictor._impute`): eksik sensör ne arıza ne sağlık kanıtıdır.
        """
        H = H.copy()
        sorun = pd.Series(False, index=H.index)
        parca = pd.Series("", index=H.index, dtype=object)
        if not self.saatlik.empty:
            anahtar = pd.MultiIndex.from_arrays([H["unit_id"], H["timestamp"].dt.floor("h")])
            kodlar = self.pencere_kodlari().reindex(anahtar)
            kodlar.index = H.index
            for sensor in self.saatlik.columns:
                kod = kodlar[sensor].fillna("")
                kotu = kod.isin(NOTRLEYEN)
                for f in SENSOR_OZNITELIK[sensor]:
                    H.loc[kotu, f] = np.nan
                sorun |= kotu
                metin = SENSOR_ADI[sensor] + " " + kod.map(NEDEN_ADI).fillna("")
                parca = parca + np.where(kotu & (parca != ""), "; ", "") + metin.where(kotu, "")
        notu = ("Sensör şüphesi: " + parca).where(sorun, "")
        return H, pd.DataFrame({"sensor_sorunu": sorun, "sensor_notu": notu})


def _hizala(g: pd.DataFrame) -> pd.DataFrame:
    """Ünite verisini düzenli 5 dakikalık ızgaraya oturtur (eksik satırlar NaN olur)."""
    g = g.drop_duplicates("timestamp").set_index("timestamp").sort_index()
    idx = pd.date_range(g.index.min().floor("h"), g.index.max(), freq=f"{STEP_MIN}min")
    return g.reindex(idx)


def _calisiyor(d: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """(çalışıyor, duruyor) maskeleri: defrost dışı; comp_on yoksa i_comp > 0,5 A."""
    if "comp_on" in d and d["comp_on"].notna().any():
        on = d["comp_on"].fillna(0).astype(float) > 0.5
    else:
        on = d["i_comp"].fillna(0) > 0.5
    defrost = pd.Series(False, index=d.index)
    if "defrost" in d:
        defrost = d["defrost"].fillna(0).astype(float) > 0.5
    return on & ~defrost, ~on & ~defrost


def _artik(x: pd.Series):
    """Yerel (5 örnek) medyandan sapma ve birim gürültü ölçeği (medyan tabanlı, aykırılara dayanıklı)."""
    r = x - x.rolling(5, center=True, min_periods=3).median()
    q50 = r.abs().quantile(0.5)  # Gauss gürültüde medyan |artık| ≈ 0,49 σ
    return r, (q50 / 0.494 if q50 == q50 else 0.0)


def _takili(x: pd.Series, n: int, tol: float) -> pd.Series:
    """Ardışık geçerli `n` örnekte (maks − min) ≤ tol olan örneklerin maskesi (pencere sonundan itibaren)."""
    v = x.dropna()
    if len(v) < n:
        return pd.Series(False, index=x.index)
    dar = (v.rolling(n).max() - v.rolling(n).min()) <= tol
    return dar.reindex(x.index, fill_value=False) & x.notna()


def _suclama(a, b, c, x, y, z, bayrak):
    """Üç sensörlük ilişki kümesi: çiftler a=(x,y), b=(z,y), c=(x,z). Ortak sensör suçlanır.

    Tek bir çift sapıyorsa (diğer çiftler sağlamsa ya da hesaplanamıyorsa) çiftin iki sensörü de şüphelidir.
    """
    sadece_a, sadece_b, sadece_c = a & ~b & ~c, b & ~a & ~c, c & ~a & ~b
    bayrak[x] |= (a & c) | sadece_a | sadece_c
    bayrak[y] |= (a & b) | sadece_a | sadece_b
    bayrak[z] |= (b & c) | sadece_b | sadece_c


def _iliskiler(d: pd.DataFrame, run: pd.Series, off: pd.Series, gecersiz: pd.DataFrame) -> pd.DataFrame:
    """Sensörler arası fiziksel ilişkilerden saatlik 'tutarsız' bayrakları (saat × sensör, bool).

    Simülatörde ekipman arızasından bağımsız olarak şu ilişkiler geçerlidir:
      A. çalışırken batarya sıcaklığı ≈ Tdoyma(emme) + 1 K;
      B. uzun duruşta (≥ 1 saat) Tdoyma(emme) ≈ oda ≈ batarya sıcaklığı;
      C. uzun duruşta Tdoyma(basma) ≈ dış ortam + 2 K ve basma hattı sıcaklığı ≈ dış ortam;
      D. duruşta akım ve titreşim ≈ 0;
      E. çalışırken emme basıncı < basma basıncı, oda > Tdoyma(emme), Tdoyma(basma) > dış ortam.
    Her ilişkinin merkezden sapmasının 12 saatlik medyanı `ILISKI_TOL_K`'yi aşarsa, çiftlerden
    ortak sensör suçlanır (`_suclama`). Takılı / kopuk / eksik örnekler (`gecersiz`) hesaba katılmaz.
    """
    t = {s: d[s].where(~gecersiz[s]) for s in SENSORLER}
    ts_suc, ts_dis = sat_temperature(t["p_suc"]), sat_temperature(t["p_dis"])
    ts_suc, ts_dis = ts_suc.where(np.isfinite(ts_suc)), ts_dis.where(np.isfinite(ts_dis))
    ort = run.copy()  # en az 4 ardışık çalışma örneği (geçici rejim dışı)
    for k in (1, 2, 3):
        ort &= run.shift(k, fill_value=False)
    uzun = off.astype(float).rolling(UZUN_DURUS).min().fillna(0) > 0.5
    n = ILISKI_PENCERE_H * PER_H
    saat = d.index.floor("h").unique()

    def medyan(seri):
        return seri.rolling(n, min_periods=ILISKI_MIN_ORNEK).median()

    def asan(seri, tol=ILISKI_TOL_K):
        return (medyan(seri).abs() > tol).resample("1h").last().reindex(saat, fill_value=False).astype(bool)

    bayrak = {s: pd.Series(False, index=saat) for s in SENSORLER}

    # A + B: emme / oda / batarya
    e1 = (ts_suc - t["t_room"]).where(uzun) - MU_DURUNCA_EMME_ODA
    e2 = (t["t_coil"] - t["t_room"]).where(uzun) - MU_DURUNCA_BATARYA_ODA
    mu3 = pd.Series(np.where(ort, -MU_CALISIRKEN_BATARYA, MU_DURUNCA_EMME_ODA - MU_DURUNCA_BATARYA_ODA),
                    index=d.index)
    e3 = (ts_suc - t["t_coil"] - mu3).where(ort | uzun)
    _suclama(asan(e1), asan(e2), asan(e3), "p_suc", "t_room", "t_coil", bayrak)

    # C: basma / dış ortam / basma hattı (yalnızca uzun duruşta)
    g1 = (ts_dis - t["t_amb"]).where(uzun) - MU_DURUNCA_BASMA_DIS
    g2 = (t["t_dis"] - t["t_amb"]).where(uzun) - MU_DURUNCA_BASMA_HAT
    g3 = (ts_dis - t["t_dis"]).where(uzun) - (MU_DURUNCA_BASMA_DIS - MU_DURUNCA_BASMA_HAT)
    _suclama(asan(g1), asan(g2), asan(g3), "p_dis", "t_amb", "t_dis", bayrak)

    # D: dururken sıfır olması gereken sensörler
    dur3 = off & off.shift(1, fill_value=False) & off.shift(2, fill_value=False)
    for s, tol in SIFIR_REF_TOL.items():
        bayrak[s] |= asan(t[s].where(dur3), tol)

    # E: imkânsız fiziksel ilişkiler (çalışırken)
    gecerli = ort & t["p_suc"].notna() & t["p_dis"].notna()
    ters = (t["p_suc"] >= t["p_dis"]).astype(float).where(gecerli)
    ters = (ters.rolling(n, min_periods=ILISKI_MIN_ORNEK).mean() >= 0.5).resample("1h").last()
    ters = ters.reindex(saat, fill_value=False).astype(bool)
    dusuk = (medyan((t["t_room"] - ts_suc).where(ort)) < EVAP_ODA_MIN_K).resample("1h").last()
    dusuk = dusuk.reindex(saat, fill_value=False).astype(bool)
    yog = (medyan((ts_dis - t["t_amb"]).where(ort)) < YOGUSMA_MIN_K).resample("1h").last()
    yog = yog.reindex(saat, fill_value=False).astype(bool)
    bayrak["p_suc"] |= ters | dusuk
    bayrak["p_dis"] |= ters | yog
    bayrak["t_room"] |= dusuk
    bayrak["t_amb"] |= yog
    return pd.DataFrame(bayrak)


def _birim_kalitesi(g: pd.DataFrame):
    """Tek ünite: (saatlik kod tablosu, silinecek örnek maskesi (5 dk × sensör), eksik oranları)."""
    d = _hizala(g)
    run, off = _calisiyor(d)
    saat = d.index.floor("h")
    saatler = saat.unique()
    kodlar = pd.DataFrame("", index=saatler, columns=SENSORLER, dtype=object)
    sil = pd.DataFrame(False, index=d.index, columns=SENSORLER)
    gecersiz = pd.DataFrame(False, index=d.index, columns=SENSORLER)
    eksik, var_sensor = {}, []
    # Kompresör / defrost geçişleri çevresinde (±2 örnek) sinyaller meşru olarak sıçrar: sıçrama aranmaz
    durum = (run.astype(int) + 2 * off.astype(int)).diff().abs().fillna(0) > 0
    gecis_surekli = durum.rolling(5, center=True, min_periods=1).max() > 0
    gecis_surekli.iloc[:6] = True  # kayıt başlangıcında yerel medyan kurulamaz
    # Kompresör sensörleri (yalnızca çalışırken okunur) ve dış sıcaklık geçişlerden etkilenmez
    gecis_komp = pd.Series(False, index=d.index)
    gecis_komp.iloc[:6] = True
    for s in SENSORLER:
        if s not in d or d[s].isna().all():
            continue
        var_sensor.append(s)
        ham = d[s].astype(float)
        lo, hi = OLASI_ARALIK[s]
        disi = ham.notna() & ((ham < lo) | (ham > hi))
        x = ham.where(~disi)
        # kompresör sensörlerinde yalnızca çalışırken değer beklenir (sh/sc dururken NaN, akım 0)
        kapi = run if s in KOMP_SENSORLERI else pd.Series(True, index=d.index)
        kapi_say = kapi.groupby(saat).sum().reindex(saatler)
        yeterli = kapi_say >= 3

        def oran(maske, kapi_say=kapi_say, yeterli=yeterli):
            return (maske.groupby(saat).sum().reindex(saatler) / kapi_say).where(yeterli, 0.0)

        eksik_h = oran(ham.isna() & kapi)
        eksik[s] = float((ham.isna() & kapi).sum() / max(int(kapi.sum()), 1))
        xg = x.where(kapi)
        gecis = gecis_komp if s in KOMP_SENSORLERI or s == "t_amb" else gecis_surekli
        r, olcek = _artik(xg)
        olcek = max(olcek, GURULTU_TABAN[s])
        sic = ((r.abs() > max(SICRAMA_K * olcek, SICRAMA_MIN[s])) & ~gecis).fillna(False)
        ra = r.abs().where(~gecis)
        gur = (ra.groupby(saat).median().reindex(saatler) > GURULTU_K * 0.494 * olcek) \
            & (ra.groupby(saat).count().reindex(saatler) >= 6)
        # tek saatlik dalgalanma sayılmaz: komşu saat de gürültülü olmalı
        gur = gur & (gur.shift(1, fill_value=False) | gur.shift(-1, fill_value=False))
        flat = _takili(xg, TAKILI_ORNEK[s], TAKILI_TOL[s])
        if s == "sc":  # ileri gaz kaçağında aşırı soğutma fiziksel tabana (0 K) dayanır: takılı sayılmaz
            flat &= x > 0.05
        sifir_sabit = flat & (x.abs() < 1e-6) if s in ("i_comp", "i_fan", "vib") else flat & False
        disi_h = disi.groupby(saat).sum().reindex(saatler)

        k = kodlar[s].copy()  # düşük önceliklerden yükseğe doğru atanır
        k[(disi_h > 0).to_numpy()] = "aralik_disi"
        k[(sic.groupby(saat).sum().reindex(saatler) > 0).to_numpy()] = "ani_sicrama"
        k[gur.fillna(False).to_numpy()] = "gurultu"
        k[(eksik_h >= EKSIK_ORAN).to_numpy()] = "veri_kaybi"
        k[(oran(flat) >= 0.5).to_numpy()] = "takili"
        k[(oran(sifir_sabit) >= 0.5).to_numpy()] = "kopuk"
        k[((disi_h / PER_H) >= ARALIK_DISI_ORAN).to_numpy()] = "kopuk"
        kodlar[s] = k
        sil[s] = disi | sic | flat
        gecersiz[s] = disi | flat | ham.isna()
    # Kayma / tutarsızlık: yalnızca daha öncelikli bir kod yoksa
    iliski = _iliskiler(d, run, off, gecersiz)
    for s in var_sensor:
        ik = iliski[s].reindex(saatler, fill_value=False).to_numpy()
        bos = kodlar[s].isin(["", "ani_sicrama", "aralik_disi", "gurultu"]).to_numpy()
        kodlar.loc[ik & bos, s] = "tutarsiz"
    return kodlar, sil, eksik


def degerlendir(raw: pd.DataFrame) -> KaliteRaporu:
    """Ham (5 dakikalık) veriyi ünite başına tarar; bkz. modül açıklaması."""
    temiz = raw.copy()
    parcalar, eksik = [], {}
    for uid, g in raw.groupby("unit_id", sort=False):
        kodlar, sil, e = _birim_kalitesi(g)
        kodlar.index = pd.MultiIndex.from_arrays([[uid] * len(kodlar), kodlar.index],
                                                 names=["unit_id", "timestamp"])
        parcalar.append(kodlar)
        eksik.update({(uid, s): v for s, v in e.items()})
        satir = sil.reindex(pd.DatetimeIndex(g["timestamp"])).fillna(False).astype(bool)
        for s in sil.columns[sil.any(axis=0)]:
            temiz.loc[g.index, s] = temiz.loc[g.index, s].where(~satir[s].to_numpy())
    return KaliteRaporu(saatlik=pd.concat(parcalar), temiz=temiz, eksik_orani=eksik)
