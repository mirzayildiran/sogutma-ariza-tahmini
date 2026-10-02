"""Arıza tahmin modeli.

Üç bileşen:
  1. Anomali tespiti (Isolation Forest) — yalnızca sağlıklı veriyle eğitilir,
     etiketsiz gerçek sahada da ilk günden çalışabilir.
  2. Arıza türü sınıflandırıcısı (Gradient Boosting).
  3. Arızaya kalan süre (saat) regresyonu.

Ekipman tipleri (soğuk oda, dondurucu, market dolabı) arasında karşılaştırılabilirlik için
modeller ham öznitelikleri değil, ünitenin **tipine ait sağlıklı ortalamadan sapmayı**
(ve tip bayraklarını) girdi alır: örn. dondurucunun normal emme basıncı ≈2 bar'dır, ama
gaz kaçağının izi her tipte "normalden düşük emme basıncı / yüksek kızgınlık"tır.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor, IsolationForest
from sklearn.preprocessing import StandardScaler

from .faults import FAULT_TYPES
from .features import FEATURE_LABELS, FEATURES, TIP_FLAGS

MAX_ETA_H = 14 * 24
PHYSICAL = [f for f in FEATURES if f not in TIP_FLAGS.values()]
FLAG_COLS = list(TIP_FLAGS.values())


def tip_of(H) -> np.ndarray:
    """Öznitelik tablosundaki tip bayraklarından ekipman tipini çıkarır (bayraksız = soğuk oda)."""
    tip = np.full(len(H), "soguk_oda", dtype=object)
    for name, col in TIP_FLAGS.items():
        tip[H[col].to_numpy() > 0.5] = name
    return tip


def health_status(health):
    if health >= 75:
        return "Normal"
    if health >= 50:
        return "İzlemede"
    return "Kritik"


# Öznitelik/model yapısı değiştiğinde artırılır; eski kayıtlı modeller yeniden eğitilir
MODEL_VERSION = 2


class FaultPredictor:
    version = 1  # sürüm alanı olmadan kaydedilmiş eski modeller

    def fit(self, H: pd.DataFrame):
        self.version = MODEL_VERSION
        healthy = (H["severity"] == 0).to_numpy()
        tips = tip_of(H)
        # Tip bazında sağlıklı referans; eğitimde görülmeyen tip için genel sağlıklı ortalama kullanılır
        self.normal_mean = H.loc[healthy, FEATURES].mean()
        self.normal_std = H.loc[healthy, FEATURES].std()
        self.baseline, self.baseline_std = {}, {}
        for tip in np.unique(tips):
            g = H.loc[healthy & (tips == tip), PHYSICAL]
            if len(g) > 1:
                self.baseline[tip], self.baseline_std[tip] = g.mean(), g.std()

        X = self._design(H)
        self.scaler = StandardScaler().fit(X[healthy])

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

    def _reference(self, H: pd.DataFrame, table, fallback) -> pd.DataFrame:
        """Her satır için ünitenin tipine ait referans değerleri (satır × fiziksel öznitelik)."""
        ref = np.empty((len(H), len(PHYSICAL)))
        tips = tip_of(H)
        for tip in np.unique(tips):
            ref[tips == tip] = table.get(tip, fallback)[PHYSICAL].to_numpy()
        return pd.DataFrame(ref, index=H.index, columns=PHYSICAL)

    def _impute(self, H: pd.DataFrame) -> np.ndarray:
        """Eksik (NaN) öznitelikleri ünite tipinin sağlıklı eğitim ortalamasıyla doldurur.

        Sahada bir sensör hiç yoksa, o sinyal ne arıza ne de sağlık kanıtı sayılır
        (nötr). IsolationForest ve StandardScaler zaten NaN kabul etmez; sınıflandırıcı
        ve regresörde de belirsiz bir dal yerine bu öngörülebilir davranışı seçiyoruz.
        Simülatör verisinde NaN olmadığından sonuçlar değişmez.
        """
        X = H[FEATURES].copy()
        X[PHYSICAL] = X[PHYSICAL].fillna(self._reference(H, self.baseline, self.normal_mean))
        return X.to_numpy()

    def _design(self, H: pd.DataFrame) -> np.ndarray:
        """Model girdisi: eksikleri doldurulmuş özniteliklerin tip referansından sapması + tip bayrakları.

        Eksik sensör tip ortalamasıyla doldurulduğu için sapması 0, yani nötrdür.
        """
        X = self._impute(H)
        base = self._reference(H, self.baseline, self.normal_mean)
        dev = X[:, : len(PHYSICAL)] - base.to_numpy()
        return np.hstack([dev, X[:, len(PHYSICAL):]])

    def classify(self, H: pd.DataFrame):
        """Saatlik sınıflandırıcı kararı (durum/eşik mantığından bağımsız ham sınıf)."""
        return self.clf.predict(self._design(H))

    def predict(self, H: pd.DataFrame) -> pd.DataFrame:
        X = self._design(H)
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
        """Aynı tipteki normal çalışmaya göre en çok sapan sinyaller (z-skoru)."""
        tip = tip_of(row[FLAG_COLS].astype(float).to_frame().T)[0]
        mean = self.baseline.get(tip, self.normal_mean)[PHYSICAL]
        std = self.baseline_std.get(tip, self.normal_std)[PHYSICAL]
        z = (row[PHYSICAL].astype(float) - mean) / std
        z = z.drop("t_amb")
        z = z[z.abs() >= 2].sort_values(key=np.abs, ascending=False).head(top_n)
        return [(FEATURE_LABELS[k], float(row[k]), float(mean[k]), float(v))
                for k, v in z.items()]
