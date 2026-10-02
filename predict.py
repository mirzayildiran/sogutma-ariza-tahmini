"""Kendi sensör CSV dosyanız için arıza tahmini (panel gerekmez).

Kullanım:  python predict.py veri.csv [--setpoint 2.0] [--gauge] [-o rapor.csv]
           [--bildirim ayarlar.json [--gonder]]   (bildirimler: docs/bildirimler.md)
Veri biçimi: docs/veri-formati.md
"""

import argparse
import sys
from pathlib import Path

import joblib
import pandas as pd

from sogutma.bildirim import AyarHatasi, Gonderici, ayar_yukle, isle
from sogutma.faults import FAULTS, fault_name
from sogutma.features import FEATURES, hourly_features
from sogutma.ingest import ValidationError, load_csv

ROOT = Path(__file__).parent
MODEL_PATH = ROOT / "models" / "predictor.joblib"
DURUM_PATH = ROOT / "data" / "bildirim_durumu.json"
EMOJI = {"Normal": "🟢", "İzlemede": "🟠", "Kritik": "🔴"}


def fmt_eta(h):
    if pd.isna(h):
        return "-"
    d, hh = divmod(int(round(h)), 24)
    return f"~{d} gün {hh} saat" if d else f"~{hh} saat"


def summarize(uid, pred, H, model):
    """Bir ünitenin son durumunu Türkçe özetler."""
    last = pred.iloc[-1]
    row = H.loc[last.name]
    status = last["status"]
    lines = [
        f"■ Ünite {uid}  ({row['timestamp']:%d.%m.%Y %H:%M} itibarıyla, {len(pred)} saatlik analiz)",
        f"  Sağlık skoru : {last['health']:.0f} / 100   {EMOJI.get(status, '')} {status}",
    ]
    if last["pred_fault"] == "normal":
        lines.append("  Durum        : Belirgin bir arıza belirtisi yok.")
    else:
        lines.append(
            f"  Tahmini arıza: {fault_name(last['pred_fault'])} (güven %{last['confidence'] * 100:.0f})"
        )
        lines.append(f"  Kalan süre   : {fmt_eta(last['eta_h'])} içinde arızalanabilir (kaba tahmin)")
        lines.append(f"  Öneri        : {FAULTS[last['pred_fault']]['oneri']}")
    dev = model.explain(row)
    if dev:
        lines.append("  Normalden en çok sapan sinyaller:")
        for ad, deger, normal, z in dev:
            lines.append(f"    - {ad}: {deger:.2f} (normal ≈ {normal:.2f}, {z:+.1f}σ)")
    # İlk kalıcı uyarı: son 6 saatin tamamı Normal dışıysa ne zamandan beri
    alarm = (pred["status"] != "Normal").to_numpy()
    if alarm[-1]:
        n = 0
        while n < len(alarm) and alarm[-1 - n]:
            n += 1
        since = H.loc[pred.index[-n], "timestamp"]
        lines.append(f"  Uyarı süresi : {since:%d.%m.%Y %H:%M} tarihinden beri Normal dışı ({n} saat)")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sensör CSV dosyasından soğutma ünitesi arıza tahmini yapar.")
    ap.add_argument("csv", help="Sensör verisi CSV dosyası (biçim: docs/veri-formati.md)")
    ap.add_argument(
        "--setpoint",
        type=float,
        default=None,
        metavar="C",
        help="Termostat set değeri (°C). CSV'de set sütunu yoksa kullanılır; "
        "verilmezse oda sıcaklığı medyanı kullanılır.",
    )
    ap.add_argument(
        "--gauge",
        action="store_true",
        help="Basınçlar efektif (gauge) ölçülmüş: +1,013 bar ile mutlağa çevrilir.",
    )
    ap.add_argument(
        "-o",
        "--output",
        default=None,
        metavar="rapor.csv",
        help="Saatlik rapor CSV dosyası (varsayılan: <girdi>_rapor.csv)",
    )
    ap.add_argument(
        "--model",
        default=str(MODEL_PATH),
        metavar="dosya.joblib",
        help="Eğitilmiş model dosyası (varsayılan: models/predictor.joblib)",
    )
    ap.add_argument(
        "--bildirim",
        default=None,
        metavar="ayarlar.json",
        help="Bildirim kuralları dosyası (örnek: examples/bildirim_ayarlari.ornek.json). "
        "Verilmezse bildirim üretilmez.",
    )
    ap.add_argument(
        "--gonder",
        action="store_true",
        help="Bildirimleri gerçekten gönderir. Verilmezse deneme modu: mesajlar yazdırılır, "
        "hiçbir şey gönderilmez.",
    )
    ap.add_argument(
        "--durum",
        default=str(DURUM_PATH),
        metavar="dosya.json",
        help="Bildirim durum dosyası; eski uyarıların tekrar gönderilmesini önler "
        "(varsayılan: data/bildirim_durumu.json)",
    )
    args = ap.parse_args(argv)
    if args.gonder and not args.bildirim:
        ap.error("--gonder için --bildirim ayarlar.json gerekir.")
    kurallar = None
    if args.bildirim:
        try:
            kurallar = ayar_yukle(args.bildirim)
        except AyarHatasi as e:
            sys.exit(f"Bildirim ayarı kullanılamıyor: {e}")

    model_path = Path(args.model)
    if not model_path.exists():
        sys.exit(f"Model bulunamadı ({model_path}).\nÖnce modeli eğitin:  python train.py")
    try:
        raw = load_csv(args.csv, gauge=args.gauge, setpoint=args.setpoint)
    except ValidationError as e:
        sys.exit(f"Veri dosyası kullanılamıyor:\n{e}\n\nBiçim için: docs/veri-formati.md")
    rapor = raw.attrs["rapor"]

    model = joblib.load(model_path)
    H = hourly_features(raw)
    if H.empty:
        sys.exit("Öznitelik üretilemedi: veri çok kısa ya da çok boşluklu (en az ~1 gün gerekir).")
    pred = model.predict(H)

    print(f"{Path(args.csv).name}: {len(raw)} örnek (5 dk), {raw['unit_id'].nunique()} ünite")
    if rapor.ozet():
        print(rapor.ozet())
    belirsiz = [f for f in FEATURES if H[f].isna().all()]
    if belirsiz:
        print(f"Not: {len(belirsiz)} öznitelik hiç hesaplanamadı (sensör eksik); nötr kabul edildi.")
    print()
    for uid in H["unit_id"].unique():
        m = (H["unit_id"] == uid).to_numpy()
        print(summarize(uid, pred[m], H[m], model))
        print()

    cikti = Path(args.output) if args.output else Path(args.csv).with_name(Path(args.csv).stem + "_rapor.csv")
    out = H[["timestamp", "unit_id"]].join(pred.round(3))
    out.to_csv(cikti, index=False)
    print(f"Saatlik rapor yazıldı: {cikti}")

    if kurallar is not None:
        print()
        gonderici = Gonderici(kurallar, deneme=not args.gonder)
        print(isle(out, kurallar, args.durum, gonderici).ozet())


if __name__ == "__main__":
    main()
