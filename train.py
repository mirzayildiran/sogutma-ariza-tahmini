"""Sentetik veriyi üretir, modeli eğitir, test filosunda değerlendirir ve
panel için demo verisini hazırlar.

Kullanım:  python train.py
"""

import json
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import classification_report, confusion_matrix

from sogutma.faults import FAULT_TYPES
from sogutma.features import FEATURES, hourly_features
from sogutma.model import FaultPredictor
from sogutma.simulator import START, demo_fleet, random_fleet, simulate_fleet

DAYS = 30
ALARM_H = 6
ROOT = Path(__file__).parent


def build(units, seed, days=DAYS):
    raw = simulate_fleet(units, days, seed)
    return raw, hourly_features(raw)


def early_warning(H, pred, units, days=DAYS):
    """Her ünite için ilk kalıcı uyarı (6 saat üst üste Normal dışı) anı."""
    rows = []
    for u in units:
        mask = (H["unit_id"] == u.unit_id).to_numpy()
        h, p = H[mask], pred[mask]
        hours = ((h["timestamp"] - START) / pd.Timedelta(hours=1)).to_numpy()
        p = p.set_axis(hours)
        alarm = (p["status"] != "Normal").astype(int)
        sustained = alarm.rolling(ALARM_H).sum() == ALARM_H
        first = sustained.idxmax() - (ALARM_H - 1) if sustained.any() else None
        fail_h = u.failure_h
        if u.fault == "normal":
            rows.append(dict(unit=u.unit_id, fault="normal", false_alarm=first is not None))
            continue
        if first is not None and first < u.fault_start_h:
            rows.append(dict(unit=u.unit_id, fault=u.fault, false_alarm=True))
            continue
        # Doğru türün tahmin edildiği ilk kalıcı uyarı
        correct = sustained & (p["pred_fault"] == u.fault)
        hit = correct.idxmax() - (ALARM_H - 1) if correct.any() else None
        rows.append(dict(
            unit=u.unit_id, fault=u.fault, false_alarm=False,
            detected=bool(hit is not None and (fail_h >= days * 24 or hit <= fail_h)),
            lead_h=None if hit is None else float(fail_h - hit),
            fails_in_window=bool(fail_h < days * 24),
        ))
    return rows


def main(n_train=120, n_test=40, days=DAYS, out_root=ROOT):
    """Varsayılanlar CLI davranışıdır; testler küçük değerler ve geçici out_root verir."""
    out_root = Path(out_root)
    DATA, MODELS = out_root / "data", out_root / "models"
    DATA.mkdir(parents=True, exist_ok=True)
    MODELS.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    print(f"1/4 Eğitim filosu simüle ediliyor ({n_train} ünite × {days} gün)...")
    train_units = random_fleet(n_train, days, seed=1)
    _, H_train = build(train_units, 1, days)

    print("2/4 Model eğitiliyor...")
    model = FaultPredictor().fit(H_train)

    print(f"3/4 Bağımsız test filosunda değerlendirme ({n_test} ünite)...")
    test_units = random_fleet(n_test, days, seed=2)
    _, H_test = build(test_units, 2, days)
    p_test = model.predict(H_test)
    y_true = H_test["label"]
    y_pred = model.clf.predict(H_test[FEATURES].to_numpy())
    report = classification_report(y_true, y_pred, labels=FAULT_TYPES,
                                   output_dict=True, zero_division=0)
    cm = confusion_matrix(y_true, y_pred, labels=FAULT_TYPES)
    ew = early_warning(H_test, p_test, test_units, days)

    faulty = [r for r in ew if r["fault"] != "normal" and not r["false_alarm"]]
    failing = [r for r in faulty if r["fails_in_window"]]
    leads = [r["lead_h"] for r in failing if r["detected"] and r["lead_h"] is not None]
    metrics = dict(
        n_train_units=len(train_units), n_test_units=len(test_units), days=days,
        accuracy=report["accuracy"], macro_f1=report["macro avg"]["f1-score"],
        per_class={f: report[f] for f in FAULT_TYPES},
        confusion_matrix=cm.tolist(), labels=FAULT_TYPES,
        early_warning=ew,
        detection_rate=(sum(r["detected"] for r in failing) / len(failing)) if failing else None,
        median_lead_h=float(np.median(leads)) if leads else None,
        false_alarm_units=sum(r["false_alarm"] for r in ew),
    )
    (MODELS / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False))
    joblib.dump(model, MODELS / "predictor.joblib")

    print("4/4 Panel için demo filosu hazırlanıyor...")
    units = demo_fleet()
    raw, H = build(units, 42, days)
    pred = model.predict(H)
    raw.to_parquet(DATA / "demo_raw.parquet")
    H.join(pred).to_parquet(DATA / "demo_hourly.parquet")
    (DATA / "demo_units.json").write_text(json.dumps(
        [dict(u.__dict__, failure_h=u.failure_h) for u in units], ensure_ascii=False, indent=2))

    print(f"\nTamamlandı ({time.time() - t0:.0f} sn)")
    print(f"  Saatlik sınıflandırma doğruluğu : {metrics['accuracy']:.1%}")
    print(f"  Makro F1                        : {metrics['macro_f1']:.3f}")
    if metrics["detection_rate"] is not None:
        print(f"  Arıza öncesi yakalama oranı     : {metrics['detection_rate']:.0%}")
    if metrics["median_lead_h"] is not None:
        print(f"  Medyan erken uyarı süresi       : {metrics['median_lead_h'] / 24:.1f} gün")
    print(f"  Yanlış alarm veren ünite        : {metrics['false_alarm_units']} / {len(test_units)}")


if __name__ == "__main__":
    main()
