"""Sentetik veriyi üretir, modeli eğitir, test filosunda değerlendirir ve
panel için demo verisini hazırlar.

Kullanım:  python train.py
"""

import json
import time
from dataclasses import asdict, replace
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import classification_report, confusion_matrix

from sogutma.analiz import ham_tahmin
from sogutma.faults import FAULT_TYPES
from sogutma.features import hourly_features
from sogutma.ingest import SCHEMA
from sogutma.model import MAX_ETA_H, MODEL_VERSION, FaultPredictor
from sogutma.simulator import (
    SENSOR_ARIZA_TURLERI,
    SENSORLER,
    START,
    TIP_TURLERI,
    SensorFault,
    demo_fleet,
    random_fleet,
    simulate_fleet,
    tip_adi,
)
from sogutma.veri_kalitesi import NOTRLEYEN

DAYS = 30
ALARM_H = 6
ROOT = Path(__file__).parent


def build(units, seed, days=DAYS):
    raw = simulate_fleet(units, days, seed)
    return raw, hourly_features(raw)


def early_warning(H, pred, units, days=DAYS, tespit_yanlis_alarmdan_sonra=False):
    """Her ünite için ilk kalıcı uyarı (6 saat üst üste Normal dışı) anı.

    `tespit_yanlis_alarmdan_sonra`: arıza başlangıcından önce yanlış alarm veren arızalı ünitede de
    (başlangıçtan sonraki) doğru tespit hesaplanır; sağlamlık değerlendirmesi için.
    """
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
            rows.append(dict(unit=u.unit_id, tip=u.tip, fault="normal", false_alarm=first is not None))
            continue
        erken = first is not None and first < u.fault_start_h
        if erken and not tespit_yanlis_alarmdan_sonra:
            rows.append(dict(unit=u.unit_id, tip=u.tip, fault=u.fault, false_alarm=True))
            continue
        # Doğru türün tahmin edildiği ilk kalıcı uyarı (arıza başlangıcından sonra başlayan)
        correct = sustained & (p["pred_fault"] == u.fault) & (p.index - (ALARM_H - 1) >= u.fault_start_h)
        hit = correct.idxmax() - (ALARM_H - 1) if correct.any() else None
        rows.append(dict(
            unit=u.unit_id, tip=u.tip, fault=u.fault, false_alarm=bool(erken),
            detected=bool(hit is not None and (fail_h >= days * 24 or hit <= fail_h)),
            lead_h=None if hit is None else float(fail_h - hit),
            fails_in_window=bool(fail_h < days * 24),
        ))
    return rows


def summarize_warnings(ew):
    """Erken uyarı satırlarından yakalama oranı, medyan süre ve yanlış alarm sayısını çıkarır."""
    faulty = [r for r in ew if r["fault"] != "normal" and not r["false_alarm"]]
    failing = [r for r in faulty if r["fails_in_window"]]
    leads = [r["lead_h"] for r in failing if r["detected"] and r["lead_h"] is not None]
    return dict(
        n_units=len(ew), n_healthy=sum(r["fault"] == "normal" for r in ew),
        n_failing=len(failing),
        detection_rate=(sum(r["detected"] for r in failing) / len(failing)) if failing else None,
        median_lead_h=float(np.median(leads)) if leads else None,
        false_alarm_units=sum(r["false_alarm"] for r in ew),
    )


def seasonal_warning_summary(ew):
    """Mevsim stresinde paydada tüm pencere içi arızaları tutar; yanlış alarm ayrıca sayılır."""
    failing = [r for r in ew if r["fault"] != "normal" and r["fails_in_window"]]
    detected = [r for r in failing if r.get("detected") and r.get("lead_h") is not None]
    leads = [r["lead_h"] for r in detected]
    return dict(
        n_failing_units=len(failing),
        detection_rate=_oran(len(detected), len(failing)),
        median_lead_h=float(np.median(leads)) if leads else None,
        false_alarm_units=sum(r["false_alarm"] for r in ew),
    )


def _unit_bootstrap_ci(values, unit_ids, seed, n_resamples=1000):
    """Saatleri değil üniteleri yeniden örnekleyerek ortalama için %95 güven aralığı."""
    values = np.asarray(values, dtype=float)
    unit_ids = np.asarray(unit_ids)
    units, inverse = np.unique(unit_ids, return_inverse=True)
    sums = np.bincount(inverse, weights=values, minlength=len(units))
    counts = np.bincount(inverse, minlength=len(units))
    rng = np.random.default_rng(seed)
    sampled = rng.integers(0, len(units), size=(n_resamples, len(units)))
    boot = sums[sampled].sum(axis=1) / counts[sampled].sum(axis=1)
    return [float(x) for x in np.percentile(boot, [2.5, 97.5])]


def _reliability_bins(classes, proba, y_true, H, n_bins=10):
    """Testte her sınıfı bire-karşı-tümü ele alan betimleyici olasılık tablosu."""
    truth = np.asarray(y_true)
    unit_ids = H["unit_id"].to_numpy()
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    class_rows, class_ece = {}, []
    for col, label in enumerate(classes):
        probability = proba[:, col]
        observed = truth == label
        bucket = np.minimum((probability * n_bins).astype(int), n_bins - 1)
        rows, ece = [], 0.0
        for index in range(n_bins):
            mask = bucket == index
            if not mask.any():
                continue
            mean_probability = float(probability[mask].mean())
            observed_rate = float(observed[mask].mean())
            count = int(mask.sum())
            ece += count / len(truth) * abs(mean_probability - observed_rate)
            rows.append({
                "alt_sinir": float(edges[index]), "ust_sinir": float(edges[index + 1]),
                "n_hours": count, "n_units": int(np.unique(unit_ids[mask]).size),
                "mean_probability": mean_probability, "observed_rate": observed_rate,
            })
        class_rows[label] = {"ece": float(ece), "bins": rows}
        class_ece.append(ece)
    return {
        "method": "one_vs_rest_equal_width",
        "n_bins": n_bins,
        "n_units": int(np.unique(unit_ids).size),
        "macro_ece": float(np.mean(class_ece)),
        "classes": class_rows,
    }


def classification_scores(model, H, y_true):
    """Kalibre edilmemiş sınıf olasılıklarının skor ve reliability ölçümleri."""
    classes = list(model.clf.classes_)
    proba = model.clf.predict_proba(model._design(H))
    truth = np.asarray([classes.index(y) for y in y_true])
    one_hot = np.eye(len(classes))[truth]
    chosen = np.clip(proba[np.arange(len(truth)), truth], 1e-15, 1.0)
    brier_rows = np.sum((proba - one_hot) ** 2, axis=1)
    logloss_rows = -np.log(chosen)
    return {
        "n_hours": int(len(truth)),
        "n_units": int(H["unit_id"].nunique()),
        "multiclass_brier": float(np.mean(brier_rows)),
        "log_loss": float(np.mean(logloss_rows)),
        "calibrated": False,
        "unit_bootstrap_95_ci": {
            "multiclass_brier": _unit_bootstrap_ci(brier_rows, H["unit_id"], seed=101),
            "log_loss": _unit_bootstrap_ci(logloss_rows, H["unit_id"], seed=102),
        },
        "reliability": _reliability_bins(classes, proba, y_true, H),
    }


def eta_scores(model, H, pred):
    """ETA hatasını alarm verilen saatlerde ve doğru arıza türü alt kümesinde ölçer."""
    alarm = (pred["pred_fault"] != "normal") & (H["hours_to_failure"] > 0)
    masks = {
        "alarm": alarm.to_numpy(),
        "dogru_ariza_turu": (alarm & (pred["pred_fault"] == H["label"])).to_numpy(),
    }

    def ozet(mask, seed):
        if not mask.any():
            return {"n_hours": 0, "n_units": 0, "mae_h": None,
                    "median_abs_error_h": None, "p90_abs_error_h": None}
        sample = H.loc[mask]
        y = sample["hours_to_failure"].clip(upper=MAX_ETA_H).to_numpy(dtype=float)
        estimate = np.expm1(model.reg.predict(model._design(sample)))
        error = np.abs(estimate - y)
        return {
            "n_hours": int(mask.sum()),
            "n_units": int(sample["unit_id"].nunique()),
            "mae_h": float(np.mean(error)),
            "mae_unit_bootstrap_95_ci_h": _unit_bootstrap_ci(error, sample["unit_id"], seed=seed),
            "median_abs_error_h": float(np.median(error)),
            "p90_abs_error_h": float(np.percentile(error, 90)),
        }

    return {
        **ozet(masks["alarm"], seed=201),
        "dogru_ariza_turu": ozet(masks["dogru_ariza_turu"], seed=202),
        "kosul": "non_normal_alarm",  # doğru arıza türü eşleşmesi ana ölçümde şart değildir
    }


def seasonal_validation(model, n_units, days, offsets=(-8.0, 8.0), seed=2):
    """Aynı sentetik filo profillerini soğuk/sıcak ortam sapmalarıyla yeniden değerlendirir.

    Bu bir mevsim simülasyonu değil, dış ortam ortalamasındaki sabit kaymaya karşı stres kontrolüdür.
    Model yeniden eğitilmez; her senaryoda yeni ham veri üretilir.
    """
    results = {}
    for offset in offsets:
        base_units = random_fleet(n_units, days, seed=seed)
        units = [replace(u, ambient_offset=u.ambient_offset + offset) for u in base_units]
        raw = simulate_fleet(units, days, seed)
        H = hourly_features(raw)
        pred = model.predict(H)
        y_true = H["label"]
        y_pred = model.classify(H)
        report = classification_report(y_true, y_pred, labels=FAULT_TYPES,
                                       output_dict=True, zero_division=0)
        warning_rows = early_warning(H, pred, units, days, tespit_yanlis_alarmdan_sonra=True)
        warning = seasonal_warning_summary(warning_rows)
        results[f"{offset:+g}C"] = dict(
            ambient_offset_c=float(offset),
            n_units=len(units),
            n_hours=int(len(H)),
            accuracy=float(report["accuracy"]),
            macro_f1=float(report["macro avg"]["f1-score"]),
            **warning,
        )
    return results


def _ingest_siniri(raw):
    """Sensör katmanı olmadan akış: yalnızca `ingest.py`'nin fiziksel sınır kontrolü (sınır dışı → eksik)."""
    raw = raw.copy()
    for c in SENSORLER:
        lo, hi = SCHEMA[c]["aralik"]
        raw[c] = raw[c].where(~((raw[c] < lo) | (raw[c] > hi)))
    return raw


def _oran(sayi, toplam):
    return sayi / toplam if toplam else None


def sensor_tespiti(kalite, units):
    """Ünite başına sensör arızası tespiti ve sağlam ünitelerde yanlış işaret.

    Enjekte edilen arıza, ilgili sensörde arıza süresi içinde işaretlenmişse tespit sayılır
    (kopuk, takılı, veri kaybı, tutarsız; ani sıçrama ve gürültüde herhangi bir işaret).
    `yanlis_sensor`: aynı pencerede başka bir sensörün de nötrlenmesi (çiftli suçlama dahil).
    """
    rows = []
    for u in units:
        g = kalite.saatlik.loc[u.unit_id]
        notr = g.isin(NOTRLEYEN)
        if not u.sensor_faults:
            rows.append(dict(unit=u.unit_id, fault=u.fault, sensor_arizasi=False,
                             isaretli=bool(notr.to_numpy().any())))
            continue
        f = u.sensor_faults[0]
        t0 = START + pd.Timedelta(hours=f.start_h)
        t1 = START + pd.Timedelta(hours=min(f.end_h(), 1e9))
        pencere = (g.index >= t0.floor("h")) & (g.index <= t1)
        kod = g[f.sensor][pencere]
        bilgi = f.kind in ("ani_sicrama", "gurultu")
        hit = kod[kod != ""] if bilgi else kod[kod.isin(NOTRLEYEN)]
        gecikme = None if hit.empty else float((hit.index[0] - t0) / pd.Timedelta(hours=1))
        digerleri = notr[pencere].drop(columns=f.sensor).to_numpy().any()
        rows.append(dict(unit=u.unit_id, fault=u.fault, sensor_arizasi=True, sensor=f.sensor, kind=f.kind,
                         tespit=not hit.empty, gecikme_h=None if gecikme is None else max(gecikme, 0.0),
                         yanlis_sensor=bool(digerleri)))
    return rows


def robustness_fleet(n_units, days, seed):
    """Rastgele filo (%50 ekipman arızalı); sağlıklı ve arızalı ekipmanlı ünitelerin her birinde sıra
    ile (sensör × arıza türü) kombinasyonları dağıtılır (en çok 66'şar ünite), kalanı kontrol grubudur.

    Böylece her sensör ve arıza türü hem sağlıklı hem arızalı ekipmanda denenir; kombinasyon sırası
    ve başlangıç zamanları ayrı bir rastgele üreteçten gelir.
    """
    units = random_fleet(n_units, days, seed, fault_ratio=0.5)
    rng = np.random.default_rng([seed, 2718])
    combos = [(s, k) for s in SENSORLER for k in SENSOR_ARIZA_TURLERI]
    for grup in ([u for u in units if u.fault == "normal"], [u for u in units if u.fault != "normal"]):
        sira = rng.permutation(len(combos))
        for u, j in zip(grup[: min(len(combos), int(len(grup) * 0.6))], sira):
            bas = rng.uniform(1.5 * 24, max(2.0 * 24, days * 24 - 8 * 24))
            sure = min(rng.uniform(5, 10) * 24, days * 24 - bas)
            u.sensor_faults = (SensorFault(combos[j][0], combos[j][1], float(bas), float(sure)),)
    return units


def robustness(model, n_units, days, seed=3):
    """Sensör arızası dayanıklılığı: aynı filo katman yokken ve varken değerlendirilir.

    Karşılaştırılan iki hat: (1) yalnızca ingest sınır kontrolü + model, (2) sensör sağlığı katmanı
    (temizleme + nötrleme) + model. Filo için bkz. `robustness_fleet`.
    """
    units = robustness_fleet(n_units, days, seed)
    raw = simulate_fleet(units, days, seed)
    H0, p0, _, _ = ham_tahmin(_ingest_siniri(raw), model, sensor_kontrolu=False)
    H1, p1, kalite, _ = ham_tahmin(raw, model)
    ew0 = early_warning(H0, p0, units, days, tespit_yanlis_alarmdan_sonra=True)
    ew1 = early_warning(H1, p1, units, days, tespit_yanlis_alarmdan_sonra=True)
    st = sensor_tespiti(kalite, units)
    rows = []
    for u, a, b, s in zip(units, ew0, ew1, st):
        rows.append(dict(unit=u.unit_id, tip=u.tip, fault=u.fault,
                         sensor=s.get("sensor"), kind=s.get("kind"),
                         false_alarm_without=bool(a["false_alarm"]), false_alarm_with=bool(b["false_alarm"]),
                         detected_without=a.get("detected"), detected_with=b.get("detected"),
                         fails_in_window=a.get("fails_in_window"),
                         sensor_detected=s.get("tespit"), sensor_delay_h=s.get("gecikme_h"),
                         wrong_sensor=s.get("yanlis_sensor"),
                         clean_flagged=s.get("isaretli")))

    def kume(sensorlu, arizali):
        return [r for r in rows
                if (r["kind"] is not None) == sensorlu and (r["fault"] != "normal") == arizali]

    sag_s, sag_t = kume(True, False), kume(False, False)
    ari_s, ari_t = kume(True, True), kume(False, True)
    yakala_s = [r for r in ari_s if r["fails_in_window"]]
    yakala_t = [r for r in ari_t if r["fails_in_window"]]
    tur = {}
    for k in SENSOR_ARIZA_TURLERI:
        h = [r for r in sag_s if r["kind"] == k]
        a = [r for r in rows if r["kind"] == k]
        tur[k] = dict(
            n_healthy=len(h), false_alarm_without=sum(r["false_alarm_without"] for r in h),
            false_alarm_with=sum(r["false_alarm_with"] for r in h),
            n_sensor_faults=len(a), sensor_detected=sum(bool(r["sensor_detected"]) for r in a))
    sf = [r for r in rows if r["kind"] is not None]
    gec = [r["sensor_delay_h"] for r in sf if r["sensor_detected"] and r["sensor_delay_h"] is not None]
    saglam = [r for r in rows if r["kind"] is None]
    return dict(
        n_units=n_units, days=days, seed=seed,
        n_sensor_fault_units=len(sf),
        healthy_with_sensor_fault=dict(
            n=len(sag_s), false_alarm_without=sum(r["false_alarm_without"] for r in sag_s),
            false_alarm_with=sum(r["false_alarm_with"] for r in sag_s)),
        healthy_without_sensor_fault=dict(
            n=len(sag_t), false_alarm_without=sum(r["false_alarm_without"] for r in sag_t),
            false_alarm_with=sum(r["false_alarm_with"] for r in sag_t)),
        faulty_with_sensor_fault=dict(
            n_failing=len(yakala_s), detected_without=sum(bool(r["detected_without"]) for r in yakala_s),
            detected_with=sum(bool(r["detected_with"]) for r in yakala_s),
            false_alarm_without=sum(r["false_alarm_without"] for r in ari_s),
            false_alarm_with=sum(r["false_alarm_with"] for r in ari_s), n_units=len(ari_s)),
        faulty_without_sensor_fault=dict(
            n_failing=len(yakala_t), detected_without=sum(bool(r["detected_without"]) for r in yakala_t),
            detected_with=sum(bool(r["detected_with"]) for r in yakala_t),
            false_alarm_without=sum(r["false_alarm_without"] for r in ari_t),
            false_alarm_with=sum(r["false_alarm_with"] for r in ari_t), n_units=len(ari_t)),
        sensor_detection=dict(
            n=len(sf), detected=sum(bool(r["sensor_detected"]) for r in sf),
            rate=_oran(sum(bool(r["sensor_detected"]) for r in sf), len(sf)),
            median_delay_h=float(np.median(gec)) if gec else None,
            wrong_sensor_units=sum(bool(r["wrong_sensor"]) for r in sf)),
        clean_units_flagged=dict(
            n=len(saglam), flagged=sum(bool(r["clean_flagged"]) for r in saglam),
            n_faulty_equipment=sum(r["fault"] != "normal" for r in saglam),
            faulty_equipment_flagged=sum(bool(r["clean_flagged"]) for r in saglam if r["fault"] != "normal")),
        by_kind=tur, units=rows,
    )


def main(n_train=120, n_test=60, days=DAYS, out_root=ROOT, n_robust=240):
    """Varsayılanlar CLI davranışıdır; testler küçük değerler ve geçici out_root verir."""
    out_root = Path(out_root)
    DATA, MODELS = out_root / "data", out_root / "models"
    DATA.mkdir(parents=True, exist_ok=True)
    MODELS.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    print(f"1/5 Eğitim filosu simüle ediliyor ({n_train} ünite × {days} gün)...")
    train_units = random_fleet(n_train, days, seed=1)
    _, H_train = build(train_units, 1, days)

    print("2/5 Model eğitiliyor...")
    model = FaultPredictor().fit(H_train)

    print(f"3/5 Bağımsız test filosunda değerlendirme ({n_test} ünite)...")
    test_units = random_fleet(n_test, days, seed=2)
    _, H_test = build(test_units, 2, days)
    p_test = model.predict(H_test)
    y_true = H_test["label"]
    y_pred = model.classify(H_test)
    report = classification_report(y_true, y_pred, labels=FAULT_TYPES,
                                   output_dict=True, zero_division=0)
    cm = confusion_matrix(y_true, y_pred, labels=FAULT_TYPES)
    ew = early_warning(H_test, p_test, test_units, days)

    overall = summarize_warnings(ew)
    per_tip = {}
    for tip in TIP_TURLERI:
        m = (H_test["tip"] == tip).to_numpy()
        if not m.any():
            continue
        per_tip[tip] = dict(
            accuracy=float((y_true.to_numpy()[m] == y_pred[m]).mean()),
            n_hours=int(m.sum()),
            **summarize_warnings([r for r in ew if r["tip"] == tip]),
        )
    metrics = dict(
        model_version=MODEL_VERSION,
        n_train_units=len(train_units), n_test_units=len(test_units), days=days,
        accuracy=report["accuracy"], macro_f1=report["macro avg"]["f1-score"],
        per_class={f: report[f] for f in FAULT_TYPES},
        confusion_matrix=cm.tolist(), labels=FAULT_TYPES,
        early_warning=ew,
        detection_rate=overall["detection_rate"],
        median_lead_h=overall["median_lead_h"],
        false_alarm_units=overall["false_alarm_units"],
        per_tip=per_tip,
        probability_scores=classification_scores(model, H_test, y_true),
        eta_error=eta_scores(model, H_test, p_test),
        seasonal_shift=seasonal_validation(model, n_test, days),
    )
    print(f"4/5 Sensör arızası dayanıklılığı ({n_robust} ünite, bir kısmında sensör arızası)...")
    metrics["robustness"] = rob = robustness(model, n_robust, days)
    (MODELS / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False))
    joblib.dump(model, MODELS / "predictor.joblib")

    print("5/5 Panel için demo filosu hazırlanıyor...")
    units = demo_fleet()
    raw = simulate_fleet(units, days, 42)
    H, pred, _, _ = ham_tahmin(raw, model)
    raw.to_parquet(DATA / "demo_raw.parquet")
    H.join(pred).to_parquet(DATA / "demo_hourly.parquet")
    (DATA / "demo_units.json").write_text(json.dumps(
        [dict(asdict(u), failure_h=u.failure_h) for u in units], ensure_ascii=False, indent=2))

    print(f"\nTamamlandı ({time.time() - t0:.0f} sn)")
    print(f"  Saatlik sınıflandırma doğruluğu : {metrics['accuracy']:.1%}")
    print(f"  Makro F1                        : {metrics['macro_f1']:.3f}")
    if metrics["detection_rate"] is not None:
        print(f"  Arıza öncesi yakalama oranı     : {metrics['detection_rate']:.0%}")
    if metrics["median_lead_h"] is not None:
        print(f"  Medyan erken uyarı süresi       : {metrics['median_lead_h'] / 24:.1f} gün")
    print(f"  Yanlış alarm veren ünite        : {metrics['false_alarm_units']} / {len(test_units)}")
    _rob_yaz(rob)
    for tip, m in per_tip.items():
        yakalama = "-" if m["detection_rate"] is None else f"%{m['detection_rate'] * 100:.0f}"
        gun = "-" if m["median_lead_h"] is None else f"{m['median_lead_h'] / 24:.1f} gün"
        print(f"  · {tip_adi(tip):<14} ({m['n_units']:>2} ünite): doğruluk {m['accuracy']:.1%}, "
              f"yakalama {yakalama}, medyan uyarı {gun}, yanlış alarm {m['false_alarm_units']}")


def _rob_yaz(r):
    """Sağlamlık sonuçlarının kısa konsol özeti (katman yok → katman var)."""
    h, f, d = r["healthy_with_sensor_fault"], r["faulty_with_sensor_fault"], r["sensor_detection"]
    c = r["clean_units_flagged"]
    print(f"  Sensör dayanıklılığı ({r['n_units']} ünite, {r['n_sensor_fault_units']} sensör arızalı, "
          "sentetik):")
    print(f"    · sensör kaynaklı yanlış alarm (sağlıklı ekipman, {h['n']} ünite): "
          f"katman yok {h['false_alarm_without']} → katman var {h['false_alarm_with']}")
    if d["rate"] is not None:
        gec = "-" if d["median_delay_h"] is None else f"{d['median_delay_h']:.0f} saat"
        print(f"    · sensör arızası tespiti: {d['detected']} / {d['n']} (%{d['rate'] * 100:.0f}), "
              f"medyan gecikme {gec}")
    print(f"    · sensör arızalı + ekipman arızalı ünitede yakalama ({f['n_failing']} ünite): "
          f"katman yok {f['detected_without']} → katman var {f['detected_with']}")
    print(f"    · sensörü sağlam ünitelerde yanlış sensör işareti: {c['flagged']} / {c['n']}")


if __name__ == "__main__":
    main()
