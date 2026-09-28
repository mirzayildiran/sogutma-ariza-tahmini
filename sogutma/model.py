"""Arıza tahmin modeli.

Üç bileşen:
  1. Anomali tespiti (Isolation Forest) — yalnızca sağlıklı veriyle eğitilir,
     etiketsiz gerçek sahada da ilk günden çalışabilir.
  2. Arıza türü sınıflandırıcısı (Gradient Boosting).
  3. Arızaya kalan süre (saat) regresyonu.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import (HistGradientBoostingClassifier,
                              HistGradientBoostingRegressor, IsolationForest)
from sklearn.preprocessing import StandardScaler

from .faults import FAULT_TYPES
from .features import FEATURE_LABELS, FEATURES

MAX_ETA_H = 14 * 24


def health_status(health):
    if health >= 75:
        return "Normal"
    if health >= 50:
        return "İzlemede"
    return "Kritik"


class FaultPredictor:
    def fit(self, H: pd.DataFrame):
        X = H[FEATURES].to_numpy()
        healthy = (H["severity"] == 0).to_numpy()

        self.scaler = StandardScaler().fit(X[healthy])
        self.normal_mean = H.loc[healthy, FEATURES].mean()
        self.normal_std = H.loc[healthy, FEATURES].std()

        self.iforest = IsolationForest(n_estimators=300, random_state=0)
        self.iforest.fit(self.scaler.transform(X[healthy]))
        s = self.iforest.score_samples(self.scaler.transform(X[healthy]))
        self.score_p50, self.score_p1 = np.percentile(s, [50, 1])

        self.clf = HistGradientBoostingClassifier(
            max_iter=200, learning_rate=0.05, max_leaf_nodes=15,
            min_samples_leaf=80, l2_regularization=1.0, random_state=0)
        self.clf.fit(X, H["label"])

        faulty = (H["label"] != "normal").to_numpy() & (H["hours_to_failure"] > 0).to_numpy()
        self.reg = HistGradientBoostingRegressor(max_iter=300, random_state=0)
        self.reg.fit(X[faulty], np.log1p(H.loc[faulty, "hours_to_failure"].clip(upper=MAX_ETA_H)))
        return self

    def predict(self, H: pd.DataFrame) -> pd.DataFrame:
        X = H[FEATURES].to_numpy()
        out = pd.DataFrame(index=H.index)

        s = self.iforest.score_samples(self.scaler.transform(X))
        out["anomaly"] = np.clip((self.score_p50 - s) / (self.score_p50 - self.score_p1) / 2, 0, 1)

        proba = pd.DataFrame(self.clf.predict_proba(X), columns=self.clf.classes_, index=H.index)
        for f in FAULT_TYPES:
            out[f"p_{f}"] = proba.get(f, 0.0)

        raw_health = 100 * (0.6 * out["p_normal"] + 0.4 * (1 - out["anomaly"]))
        out["health"] = raw_health.groupby(H["unit_id"]).transform(
            lambda x: x.ewm(span=12).mean()).round(1)
        out["status"] = out["health"].map(health_status)

        fault_cols = [f"p_{f}" for f in FAULT_TYPES[1:]]
        top = out[fault_cols].idxmax(axis=1).str[2:]
        out["pred_fault"] = np.where(out["status"] == "Normal", "normal", top)
        out["confidence"] = np.where(out["pred_fault"] == "normal",
                                     out["p_normal"], out[fault_cols].max(axis=1)).round(3)
        eta = np.expm1(self.reg.predict(X))
        out["eta_h"] = np.where(out["pred_fault"] == "normal", np.nan, eta.round(0))
        return out

    def explain(self, row: pd.Series, top_n=4):
        """Normal çalışmaya göre en çok sapan sinyaller (z-skoru)."""
        z = (row[FEATURES].astype(float) - self.normal_mean) / self.normal_std
        z = z.drop("t_amb")
        z = z[z.abs() >= 2].sort_values(key=np.abs, ascending=False).head(top_n)
        return [(FEATURE_LABELS[k], float(row[k]), float(self.normal_mean[k]), float(v))
                for k, v in z.items()]
