"""Basitleştirilmiş, fizik esaslı soğutma ünitesi simülatörü.

Üç ekipman tipi simüle edilir: soğuk oda, dondurucu oda ve market dolabı (bkz. `TIPLER`).
Her ünite; termostat kontrollü bir kompresör, periyodik defrost, kapı/müşteri erişimi
ve günlük dış ortam sıcaklığı değişimiyle simüle edilir. Arızalar zamanla
ilerleyen bir şiddet (0 → 1) olarak enjekte edilir; şiddet 1'e ulaştığında
ünite artık sıcaklığı tutamaz, bu an "arıza anı" kabul edilir.

Soğutucu akışkan: R404A (doyma basıncı için Antoine tipi yaklaşık eğri).
Tip parametreleri gerçek bir ekipman kataloğundan değil, makul mertebelere göre
elle seçilmiştir; amaç tipler arası yönsel farkları (basınç, sıkıştırma oranı, yük
profili) taklit etmektir.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from .faults import FAULT_TYPES

STEP_MIN = 5
DT_H = STEP_MIN / 60
START = pd.Timestamp("2026-01-01")

# R404A doyma eğrisi: ln P = A − B / (T + C)   (P bar mutlak, T °C).
# Katsayılar yaklaşık R404A doyma değerlerine uydurulmuştur: −46 °C'de ≈1,01 bar (normal kaynama
# noktası), −30 °C'de ≈2 bar, 0 °C'de ≈5,8 bar, +20 °C'de ≈10,3 bar, +40 °C'de ≈18 bar.
# Soğuk oda aralığında (−10…+45 °C) eski üstel yaklaşıma yakındır (±%5); dondurucu
# evaporasyon sıcaklıklarında (−30 °C civarı) eski eğri basıncı ≈%20 fazla veriyordu.
_ANTOINE_A = 11.1093
_ANTOINE_B = 2750.32
_ANTOINE_C = 294.261


def sat_pressure(t_c):
    """R404A yaklaşık doyma basıncı (bar, mutlak)."""
    return np.exp(_ANTOINE_A - _ANTOINE_B / (t_c + _ANTOINE_C))


def sat_temperature(p_bar):
    """`sat_pressure` fonksiyonunun tam tersi: basınçtan (bar, mutlak) doyma sıcaklığı (°C)."""
    return _ANTOINE_B / (_ANTOINE_A - np.log(p_bar)) - _ANTOINE_C


@dataclass(frozen=True)
class TipParametreleri:
    """Bir ekipman tipinin sabit parametreleri (tüm birimler K, saat, A, bar)."""

    ad: str
    setpoint: float  # varsayılan hedef sıcaklık (°C)
    setpoint_aralik: Tuple[float, float]  # rastgele filoda hedef aralığı
    hist: float  # termostat yarı bandı: hedef ± hist (K)
    defrost_her_h: float  # defrost aralığı (saat)
    defrost_sure_h: float  # defrost süresi (saat)
    defrost_isi: float  # defrost sırasında odaya ısı girişi (K/saat)
    ua_gun: float  # yoğun saatlerde duvar/dolap ısı geçirgenliği (1/saat)
    ua_gece: float  # sakin saatlerde (market dolabında gece perdesiyle) aynı katsayı
    yogun: Tuple[float, float]  # yoğun saat aralığı [başlangıç, bitiş] (saat)
    yuk_degisim: float  # yoğun saatlerdeki yük dalgalanma genliği (oransal)
    kapi_yogun: float  # yoğun saatte adım başına kapı/erişim olasılığı
    kapi_sakin: float  # sakin saatte aynı olasılık
    kapi_isi: float  # bir erişimde odaya ısı girişi (K/saat)
    sogutma: float  # kapasite = 1 iken soğutma hızı (K/saat)
    amb_kap: float  # dış ortam 1 K artınca kapasite kaybı (oransal)
    td_evap: float  # oda − evaporasyon sıcaklığı farkı (K)
    sh0: float  # sağlıklı kızgınlık (K)
    sc0: float  # sağlıklı aşırı soğutma (K)
    cond_fark: float  # yoğuşma − dış ortam farkı (K)
    t_dis_fark: float  # basma hattı sıcaklığı − yoğuşma sıcaklığı (K)
    i0: float  # kompresör akımı sabit terimi (A)
    i_k: float  # kompresör akımı basınç farkı katsayısı (A/bar)
    i_fan0: float  # sağlıklı fan akımı (A)


TIPLER = {
    # Soğuk oda: orta sıcaklık (≈0…5 °C), 6 saatte bir 20 dk defrost, gündüz kapı trafiği.
    "soguk_oda": TipParametreleri(
        ad="Soğuk oda", setpoint=2.0, setpoint_aralik=(0.0, 5.0), hist=1.0,
        defrost_her_h=6, defrost_sure_h=20 / 60, defrost_isi=1.5,
        ua_gun=0.06, ua_gece=0.06, yogun=(7, 19), yuk_degisim=0.0,
        kapi_yogun=0.08, kapi_sakin=0.01, kapi_isi=2.5,
        sogutma=4.0, amb_kap=0.012, td_evap=8.0, sh0=6.0, sc0=5.0,
        cond_fark=10.0, t_dis_fark=25.0, i0=7.0, i_k=0.35, i_fan0=1.2),
    # Dondurucu oda: ≈−18…−22 °C. Evaporasyon ≈−30 °C (≈2 bar) olduğundan sıkıştırma oranı ≈7,
    # basma hattı sıcaklığı yüksek, kapasite düşük ve dış sıcaklığa daha duyarlıdır. Elektrikli
    # defrost seyrek (8 saatte bir) ama uzun (30 dk) ve ısıtıcı gücü büyüktür; ΔT büyük
    # olduğundan ısı kazancı hedefe göre yüksektir, kapı açılışları ise daha seyrektir.
    "dondurucu": TipParametreleri(
        ad="Dondurucu", setpoint=-20.0, setpoint_aralik=(-22.0, -18.0), hist=1.0,
        defrost_her_h=8, defrost_sure_h=30 / 60, defrost_isi=2.5,
        ua_gun=0.034, ua_gece=0.034, yogun=(7, 19), yuk_degisim=0.0,
        kapi_yogun=0.05, kapi_sakin=0.01, kapi_isi=3.0,
        sogutma=3.8, amb_kap=0.018, td_evap=9.0, sh0=8.0, sc0=5.0,
        cond_fark=11.0, t_dis_fark=38.0, i0=6.0, i_k=0.45, i_fan0=1.2),
    # Market dolabı (açık veya camlı kapılı reyon dolabı): küçük kapasite, düşük ısıl kütle.
    # Mağaza saatlerinde (08–22) yük yüksek ve değişkendir (müşteri erişimi, aydınlatma, hava
    # sızması); gece perdesiyle yük düşer. Dar termostat bandı kısa ve sık çevrim üretir.
    "market_dolabi": TipParametreleri(
        ad="Market dolabı", setpoint=4.0, setpoint_aralik=(2.0, 6.0), hist=0.5,
        defrost_her_h=6, defrost_sure_h=15 / 60, defrost_isi=1.0,
        ua_gun=0.11, ua_gece=0.045, yogun=(8, 22), yuk_degisim=0.3,
        kapi_yogun=0.30, kapi_sakin=0.0, kapi_isi=1.2,
        sogutma=6.0, amb_kap=0.010, td_evap=10.0, sh0=7.0, sc0=4.0,
        cond_fark=12.0, t_dis_fark=25.0, i0=3.5, i_k=0.25, i_fan0=0.8),
}
TIP_TURLERI = list(TIPLER)


def tip_adi(tip):
    return TIPLER[tip].ad


@dataclass
class Unit:
    unit_id: str
    name: str
    setpoint: Optional[float] = None  # verilmezse tipin varsayılan hedefi
    capacity: float = 1.0
    ambient_offset: float = 0.0
    door_traffic: float = 1.0
    fault: str = "normal"
    fault_start_h: float = 0.0
    fault_duration_h: float = 1.0
    tip: str = "soguk_oda"

    def __post_init__(self):
        if self.tip not in TIPLER:
            raise ValueError(f"Bilinmeyen ekipman tipi: {self.tip!r} (geçerli: {', '.join(TIPLER)})")
        if self.setpoint is None:
            self.setpoint = TIPLER[self.tip].setpoint

    @property
    def failure_h(self):
        if self.fault == "normal":
            return np.nan
        return self.fault_start_h + self.fault_duration_h

    def severity(self, t_h):
        if self.fault == "normal" or t_h < self.fault_start_h:
            return 0.0
        x = min((t_h - self.fault_start_h) / self.fault_duration_h, 1.0)
        # Başta yavaş, sona doğru hızlanan bozulma
        return x ** 1.6


COLUMNS = [
    "t_amb", "t_room", "t_coil", "p_suc", "p_dis", "sh", "sc",
    "i_comp", "i_fan", "vib", "t_dis", "comp_on", "defrost", "door_open", "severity",
]


def simulate_unit(unit: Unit, days: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = int(days * 24 * 60 / STEP_MIN)
    noise = rng.standard_normal((n, 12))
    door_draw = rng.random(n)
    day_offsets = rng.normal(0, 2.0, days + 1)

    P = TIPLER[unit.tip]
    sp = unit.setpoint
    t_room = sp + rng.uniform(-1, 1)
    # Yük dalgalanması gürültüsü en sona eklenir: önceki rastgele akış tip eklenmeden öncekiyle aynı kalır
    load_draw = rng.standard_normal(n)
    load_state = 0.0
    comp_on = False
    p_suc = sat_pressure(t_room)
    p_dis = sat_pressure(25.0)
    t_coil = t_room
    t_dis = 30.0

    out = np.empty((n, len(COLUMNS)))
    for i in range(n):
        z = noise[i]
        t_h = i * DT_H
        hour = t_h % 24
        t_amb = (24 + unit.ambient_offset + day_offsets[int(t_h // 24)]
                 + 6 * np.sin(2 * np.pi * (hour - 9) / 24) + 0.3 * z[0])

        sev = unit.severity(t_h)
        L = sev if unit.fault == "gaz_kacagi" else 0.0
        F = sev if unit.fault == "kondenser_kirlenmesi" else 0.0
        I = sev if unit.fault == "evaporator_buzlanma" else 0.0  # noqa: E741
        W = sev if unit.fault == "kompresor_asinmasi" else 0.0
        N = sev ** 1.5 if unit.fault == "fan_arizasi" else 0.0

        defrost = (t_h % P.defrost_her_h) < P.defrost_sure_h
        if defrost:
            comp_on = False
        elif t_room > sp + P.hist:
            comp_on = True
        elif t_room < sp - P.hist:
            comp_on = False

        busy = P.yogun[0] <= hour <= P.yogun[1]
        door_open = door_draw[i] < (P.kapi_yogun if busy else P.kapi_sakin) * unit.door_traffic

        ua = P.ua_gun if busy else P.ua_gece
        if P.yuk_degisim:
            # Yavaş değişen (AR(1), birim varyanslı) mağaza yükü: yalnızca yoğun saatlerde etkili
            load_state = 0.97 * load_state + 0.24 * load_draw[i]
            if busy:
                ua *= max(1 + P.yuk_degisim * load_state, 0.3)

        cap = (unit.capacity * (1 - 0.68 * L) * (1 - 0.7 * I) * (1 - 0.6 * F)
               * (1 - 0.65 * W) * (1 - 0.65 * N) * (1 - P.amb_kap * (t_amb - 24)))
        gain = (ua * (20 + 0.3 * (t_amb - 24) - t_room)
                + (P.kapi_isi if door_open else 0.0) + (P.defrost_isi if defrost else 0.0))
        cool = P.sogutma * cap if comp_on else 0.0
        t_room += DT_H * (gain - cool) + 0.03 * z[1]

        if comp_on:
            t_evap = t_room - P.td_evap - 7 * I - 9 * L + 0.3 * z[2]
            t_cond = t_amb + P.cond_fark + 14 * F + 12 * N + 4 * W - 4 * L + 0.4 * z[3]
            p_suc += 0.7 * (sat_pressure(t_evap) - p_suc)
            p_dis += 0.7 * (sat_pressure(t_cond) - p_dis)
            t_coil += 0.6 * (t_evap + 1 - t_coil)
            sh = max(P.sh0 + 18 * L - 4 * I + 0.5 * z[4], 0.5)
            sc = max(P.sc0 - 4.5 * L + 1.0 * F + 0.4 * z[5], 0.0)
            i_comp = P.i0 + P.i_k * (p_dis - p_suc) + 3 * W - 1.5 * L + 0.2 * z[6]
            i_fan = P.i_fan0 + 0.9 * N + 0.03 * z[7]
            vib = 1.5 + 5 * W ** 2 + 0.15 * z[8]
            t_dis += 0.6 * (t_cond + P.t_dis_fark + 20 * L + 18 * W + 0.8 * z[9] - t_dis)
        else:
            p_suc += 0.3 * (sat_pressure(t_room) - p_suc)
            p_dis += 0.3 * (sat_pressure(t_amb + 2) - p_dis)
            # Defrostta rezistans bataryayı ısıtır; buzlanma arızasında yetersiz kalır
            coil_target = 10 * (1 - 0.8 * I) if defrost else t_room
            t_coil += 0.4 * (coil_target - t_coil)
            sh = sc = np.nan
            i_comp = 0.0
            i_fan = 0.0
            vib = abs(0.05 + 0.02 * z[8])
            t_dis += 0.3 * (t_amb - t_dis)

        out[i] = (t_amb, t_room, t_coil + 0.1 * z[10], p_suc + 0.03 * z[11],
                  p_dis, sh, sc, i_comp, i_fan, vib, t_dis,
                  comp_on, defrost, door_open, sev)

    df = pd.DataFrame(out, columns=COLUMNS)
    df.insert(0, "timestamp", START + pd.to_timedelta(np.arange(n) * STEP_MIN, unit="min"))
    df.insert(1, "unit_id", unit.unit_id)
    df["setpoint"] = sp
    df["tip"] = unit.tip
    df["fault"] = unit.fault
    t_hours = np.arange(n) * DT_H
    df["hours_to_failure"] = np.clip(unit.failure_h - t_hours, 0, None)
    for c in ("comp_on", "defrost", "door_open"):
        df[c] = df[c].astype(bool)
    return df


def simulate_fleet(units, days, seed):
    return pd.concat(
        [simulate_unit(u, days, seed * 1000 + k) for k, u in enumerate(units)],
        ignore_index=True,
    )


# Rastgele filoda ekipman tipi dağılımı
TIP_AGIRLIK = {"soguk_oda": 0.5, "dondurucu": 0.25, "market_dolabi": 0.25}


def _tip_dagilimi(n_units, agirlik, seed):
    """Tiplere en büyük kalan yöntemiyle kesin sayılar ayırır, sırayı karıştırır.

    Ayrı bir üreteç kullanıldığı için tip ataması, arıza/ünite parametrelerinin
    rastgele akışını değiştirmez (yalnızca soğuk odalı bir filo eskisiyle aynı üretilir).
    """
    adlar = list(agirlik)
    w = np.array([agirlik[a] for a in adlar], dtype=float)
    w = w / w.sum()
    kesin = w * n_units
    sayi = np.floor(kesin).astype(int)
    for k in np.argsort(-(kesin - sayi), kind="stable")[: n_units - sayi.sum()]:
        sayi[k] += 1
    tipler = np.repeat(adlar, sayi)
    np.random.default_rng([seed, 7919]).shuffle(tipler)
    return [str(t) for t in tipler]


def random_fleet(n_units, days, seed, fault_ratio=0.75, tip_agirlik=None):
    """Eğitim/test için rastgele tip ve arıza senaryolu filo üretir.

    Varsayılan tip dağılımı: %50 soğuk oda, %25 dondurucu, %25 market dolabı
    (`tip_agirlik` ile değiştirilebilir). Her tipte tüm arıza türleri mümkündür.
    """
    rng = np.random.default_rng(seed)
    tipler = _tip_dagilimi(n_units, tip_agirlik or TIP_AGIRLIK, seed)
    units = []
    for k in range(n_units):
        P = TIPLER[tipler[k]]
        fault = rng.choice(FAULT_TYPES[1:]) if rng.random() < fault_ratio else "normal"
        dur = rng.uniform(4, 14) * 24
        start = rng.uniform(3 * 24, days * 24 - 0.5 * dur)
        units.append(Unit(
            unit_id=f"S{seed}-{k:03d}", name=f"Ünite {k}",
            setpoint=rng.uniform(*P.setpoint_aralik), capacity=rng.uniform(0.9, 1.15),
            ambient_offset=rng.normal(0, 3), door_traffic=rng.uniform(0.5, 1.6),
            fault=str(fault), fault_start_h=start, fault_duration_h=dur, tip=tipler[k],
        ))
    return units


def demo_fleet():
    """Panelde gösterilecek, senaryoları sabit 12 ünite: 8 soğuk oda, 2 dondurucu, 2 market dolabı."""
    d = 24
    return [
        Unit("A1", "Soğuk Oda A1 · Et", 1.0, 1.05, 1.0, 1.4, "gaz_kacagi", 12 * d, 9 * d),
        Unit("A2", "Soğuk Oda A2 · Süt Ürünleri", 3.0, 1.0, -1.0, 1.2),
        Unit("B1", "Soğuk Oda B1 · Sebze-Meyve", 4.0, 1.0, 3.5, 0.8, "kondenser_kirlenmesi", 8 * d, 14 * d),
        Unit("B2", "Soğuk Oda B2 · İçecek", 4.0, 0.95, 2.0, 1.5),
        Unit("C1", "Soğuk Oda C1 · Şarküteri", 2.0, 1.1, 0.0, 1.0, "evaporator_buzlanma", 16 * d, 7 * d),
        Unit("C2", "Soğuk Oda C2 · Balık", 0.5, 1.1, -2.0, 0.9, "kompresor_asinmasi", 5 * d, 20 * d),
        Unit("D1", "Soğuk Oda D1 · Ana Depo", 3.0, 1.0, 1.5, 0.6, "fan_arizasi", 18 * d, 6 * d),
        Unit("D2", "Soğuk Oda D2 · Çiçek", 5.0, 0.95, 0.5, 0.7),
        Unit("E1", "Dondurucu E1 · Dondurulmuş Gıda", -20.0, 1.0, 1.0, 0.8, tip="dondurucu"),
        Unit("E2", "Dondurucu E2 · Dondurma", -22.0, 1.05, 0.0, 1.0, "kompresor_asinmasi", 7 * d, 16 * d,
             tip="dondurucu"),
        Unit("F1", "Market Dolabı F1 · Süt Reyonu", 4.0, 1.0, 1.0, 1.0, tip="market_dolabi"),
        Unit("F2", "Market Dolabı F2 · Hazır Yemek", 3.0, 1.0, 2.0, 1.2, "gaz_kacagi", 11 * d, 10 * d,
             tip="market_dolabi"),
    ]
