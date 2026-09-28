"""Basitleştirilmiş, fizik esaslı soğuk oda simülatörü.

Her ünite; termostat kontrollü bir kompresör, periyodik defrost, kapı açılışları
ve günlük dış ortam sıcaklığı değişimiyle simüle edilir. Arızalar zamanla
ilerleyen bir şiddet (0 → 1) olarak enjekte edilir; şiddet 1'e ulaştığında
ünite artık sıcaklığı tutamaz, bu an "arıza anı" kabul edilir.

Soğutucu akışkan: R404A (doyma basıncı için yaklaşık üstel eğri).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .faults import FAULT_TYPES

STEP_MIN = 5
DT_H = STEP_MIN / 60
DEFROST_EVERY_H = 6
DEFROST_LEN_H = 20 / 60
START = pd.Timestamp("2026-01-01")


def sat_pressure(t_c):
    """R404A yaklaşık doyma basıncı (bar, mutlak)."""
    return np.exp(1.766 + 0.02838 * t_c)


def sat_temperature(p_bar):
    return (np.log(p_bar) - 1.766) / 0.02838


@dataclass
class Unit:
    unit_id: str
    name: str
    setpoint: float = 2.0
    capacity: float = 1.0
    ambient_offset: float = 0.0
    door_traffic: float = 1.0
    fault: str = "normal"
    fault_start_h: float = 0.0
    fault_duration_h: float = 1.0

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

    sp = unit.setpoint
    t_room = sp + rng.uniform(-1, 1)
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
        I = sev if unit.fault == "evaporator_buzlanma" else 0.0
        W = sev if unit.fault == "kompresor_asinmasi" else 0.0
        N = sev ** 1.5 if unit.fault == "fan_arizasi" else 0.0

        defrost = (t_h % DEFROST_EVERY_H) < DEFROST_LEN_H
        if defrost:
            comp_on = False
        elif t_room > sp + 1.0:
            comp_on = True
        elif t_room < sp - 1.0:
            comp_on = False

        busy = 7 <= hour <= 19
        door_open = door_draw[i] < (0.08 if busy else 0.01) * unit.door_traffic

        cap = (unit.capacity * (1 - 0.68 * L) * (1 - 0.7 * I) * (1 - 0.6 * F)
               * (1 - 0.65 * W) * (1 - 0.65 * N) * (1 - 0.012 * (t_amb - 24)))
        gain = (0.06 * (20 + 0.3 * (t_amb - 24) - t_room)
                + (2.5 if door_open else 0.0) + (1.5 if defrost else 0.0))
        cool = 4.0 * cap if comp_on else 0.0
        t_room += DT_H * (gain - cool) + 0.03 * z[1]

        if comp_on:
            t_evap = t_room - 8 - 7 * I - 9 * L + 0.3 * z[2]
            t_cond = t_amb + 10 + 14 * F + 12 * N + 4 * W - 4 * L + 0.4 * z[3]
            p_suc += 0.7 * (sat_pressure(t_evap) - p_suc)
            p_dis += 0.7 * (sat_pressure(t_cond) - p_dis)
            t_coil += 0.6 * (t_evap + 1 - t_coil)
            sh = max(6 + 18 * L - 4 * I + 0.5 * z[4], 0.5)
            sc = max(5 - 4.5 * L + 1.0 * F + 0.4 * z[5], 0.0)
            i_comp = 7 + 0.35 * (p_dis - p_suc) + 3 * W - 1.5 * L + 0.2 * z[6]
            i_fan = 1.2 + 0.9 * N + 0.03 * z[7]
            vib = 1.5 + 5 * W ** 2 + 0.15 * z[8]
            t_dis += 0.6 * (t_cond + 25 + 20 * L + 18 * W + 0.8 * z[9] - t_dis)
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


def random_fleet(n_units, days, seed, fault_ratio=0.75):
    """Eğitim/test için rastgele arıza senaryolu filo üretir."""
    rng = np.random.default_rng(seed)
    units = []
    for k in range(n_units):
        fault = rng.choice(FAULT_TYPES[1:]) if rng.random() < fault_ratio else "normal"
        dur = rng.uniform(4, 14) * 24
        start = rng.uniform(3 * 24, days * 24 - 0.5 * dur)
        units.append(Unit(
            unit_id=f"S{seed}-{k:03d}", name=f"Ünite {k}",
            setpoint=rng.uniform(0, 5), capacity=rng.uniform(0.9, 1.15),
            ambient_offset=rng.normal(0, 3), door_traffic=rng.uniform(0.5, 1.6),
            fault=str(fault), fault_start_h=start, fault_duration_h=dur,
        ))
    return units


def demo_fleet():
    """Panelde gösterilecek, senaryoları sabit 8 soğuk oda."""
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
    ]
