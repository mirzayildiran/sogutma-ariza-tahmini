"""5 dakikalık ham sensör verisinden saatlik model özniteliklerinin çıkarımı."""

import numpy as np
import pandas as pd

from .simulator import sat_temperature

FEATURE_LABELS = {
    "p_suc": "Emme basıncı (bar)",
    "p_dis": "Basma basıncı (bar)",
    "sh": "Kızgınlık / superheat (K)",
    "sc": "Aşırı soğutma / subcooling (K)",
    "i_comp": "Kompresör akımı (A)",
    "i_fan": "Kondenser fan akımı (A)",
    "vib": "Kompresör titreşimi (mm/s)",
    "t_dis": "Basma hattı sıcaklığı (°C)",
    "cond_approach": "Yoğuşma − dış ortam farkı (K)",
    "evap_delta": "Oda − evaporatör farkı (K)",
    "duty": "Kompresör çalışma oranı",
    "starts": "Saatlik kompresör kalkışı",
    "t_room_dev": "Oda sıcaklığı sapması (K)",
    "coil_defrost_peak": "Defrostta batarya tepe sıcaklığı (°C)",
    "t_amb": "Dış ortam sıcaklığı (°C)",
}
FEATURES = list(FEATURE_LABELS)
RUNNING_ONLY = ["p_suc", "p_dis", "sh", "sc", "i_comp", "i_fan", "vib", "t_dis"]
WINDOW_H = 12


LABEL_COLS = ["severity", "hours_to_failure", "fault"]
# Bir saatin öznitelik üretmesi için gereken en az örnek sayısı (12 örneğin yarısı)
MIN_SAMPLES_PER_HOUR = 6


def _col(g, name):
    """Sütun yoksa (isteğe bağlı sensör eksikse) NaN serisi döndürür."""
    return g[name] if name in g else pd.Series(np.nan, index=g.index)


def _unit_features(g):
    g = g.set_index("timestamp")
    comp_on = g["comp_on"].astype(bool)
    defrost = g["defrost"].astype(bool) if "defrost" in g else pd.Series(False, index=g.index)
    running = comp_on & ~defrost
    x = pd.DataFrame(index=g.index)
    for c in RUNNING_ONLY:
        x[c] = _col(g, c).where(running)
    x["cond_approach"] = (sat_temperature(g["p_dis"]) - g["t_amb"]).where(running)
    x["evap_delta"] = (g["t_room"] - sat_temperature(g["p_suc"])).where(running)
    x["duty"] = running.astype(float)
    x["starts"] = (comp_on.astype(int).diff() == 1).astype(float)
    x["t_room_dev"] = g["t_room"] - g["setpoint"]
    x["coil_defrost_peak"] = _col(g, "t_coil").where(defrost)
    x["t_amb"] = g["t_amb"]

    agg = {c: "mean" for c in FEATURES}
    agg.update(starts="sum", coil_defrost_peak="max")
    h = x.resample("1h").agg(agg)
    feats = h.rolling(WINDOW_H, min_periods=2).mean()
    # Defrost 6 saatte bir: son 12 saatteki en yüksek batarya sıcaklığı
    feats["coil_defrost_peak"] = h["coil_defrost_peak"].rolling(12, min_periods=1).max()
    feats = feats.ffill().bfill()
    # Verinin büyük kısmı eksik olan saatler (uzun boşluklar) çıktıya alınmaz
    n_obs = g["t_room"].resample("1h").count()
    feats = feats[n_obs >= MIN_SAMPLES_PER_HOUR]

    # İlk pencere dolana kadar (ısınma süresi) öznitelikler güvenilir değil
    feats = feats.iloc[WINDOW_H:]
    # Etiketler yalnızca simülatör / etiketli verilerde bulunur
    if all(c in g for c in LABEL_COLS):
        labels = g[LABEL_COLS].resample("1h").agg(
            {"severity": "max", "hours_to_failure": "min", "fault": "first"})
        feats = feats.join(labels, how="inner")
    return feats


def hourly_features(raw: pd.DataFrame, label_threshold=0.1) -> pd.DataFrame:
    parts = []
    for uid, g in raw.groupby("unit_id", sort=False):
        f = _unit_features(g)
        f.insert(0, "unit_id", uid)
        parts.append(f)
    H = pd.concat(parts).reset_index()
    if "severity" in H:
        H["label"] = np.where(H["severity"] >= label_threshold, H["fault"], "normal")
    return H
