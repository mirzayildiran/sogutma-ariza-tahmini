"""examples/ornek_veri.csv dosyasını simülatörden yeniden üretir.

Tek ünite, 10 gün; 5. günden itibaren gaz kaçağı gelişir. Dosya, Türkçe Excel'in
"CSV (noktalı virgülle ayrılmış)" dışa aktarımını taklit eder: ayraç ';', ondalık ',',
Türkçe sütun adları, gg.aa.yyyy ss:dd tarih biçimi.

Kullanım:  python examples/ornek_veri_uret.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sogutma.simulator import Unit, simulate_unit  # noqa: E402

GUN = 10
COLUMNS = {  # simülatör sütunu: (Türkçe başlık, ondalık hane)
    "t_room": ("Oda Sıcaklığı (°C)", 2),
    "t_amb": ("Dış Ortam Sıcaklığı (°C)", 1),
    "p_suc": ("Emme Basıncı (bar)", 2),
    "p_dis": ("Basma Basıncı (bar)", 2),
    "i_comp": ("Kompresör Akımı (A)", 2),
    "t_coil": ("Evaporatör Sıcaklığı (°C)", 1),
    "sh": ("Kızgınlık (K)", 1),
    "sc": ("Aşırı Soğutma (K)", 1),
    "i_fan": ("Fan Akımı (A)", 2),
    "vib": ("Titreşim (mm/s)", 2),
    "t_dis": ("Basma Sıcaklığı (°C)", 1),
    "comp_on": ("Kompresör Durumu", 0),
    "defrost": ("Defrost", 0),
    "door_open": ("Kapı Açık", 0),
    "setpoint": ("Set Değeri (°C)", 1),
}


def uret(yol):
    birim = Unit(
        "A1",
        "Örnek Et Odası",
        setpoint=1.0,
        capacity=1.05,
        ambient_offset=1.0,
        door_traffic=1.3,
        fault="gaz_kacagi",
        fault_start_h=4 * 24,
        fault_duration_h=7 * 24,
    )
    raw = simulate_unit(birim, GUN, seed=7)
    out = raw[["timestamp"]].copy()
    out["timestamp"] = raw["timestamp"].dt.strftime("%d.%m.%Y %H:%M")
    out = out.rename(columns={"timestamp": "Tarih Saat"})
    for kaynak, (baslik, hane) in COLUMNS.items():
        col = raw[kaynak]
        out[baslik] = col.astype(int) if col.dtype == bool else col.round(hane)
    out.to_csv(yol, sep=";", decimal=",", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    hedef = ROOT / "examples" / "ornek_veri.csv"
    uret(hedef)
    print(f"{hedef} yazıldı ({hedef.stat().st_size / 1024:.0f} KB)")
