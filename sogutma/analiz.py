"""Kullanıcı CSV'sinden saatlik tahmine giden ortak akış (predict.py ve panel sayfası kullanır).

CSV okuma/doğrulama (ingest) → saatlik öznitelikler → model tahmini. Veri kullanılamıyorsa
`ValidationError` (ingest) ya da `YetersizVeri` fırlatılır; ikisi de Türkçe mesaj taşır.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from .faults import FAULTS
from .features import FEATURES, hourly_features
from .ingest import IngestReport, load_csv


class YetersizVeri(ValueError):
    """Dosya geçerli ama saatlik öznitelik üretmek için çok kısa ya da çok boşluklu."""


@dataclass
class Analiz:
    raw: pd.DataFrame  # 5 dakikalık, doğrulanmış ham veri
    H: pd.DataFrame  # saatlik öznitelikler
    pred: pd.DataFrame  # model çıktısı (H ile aynı indeks)
    rapor: IngestReport
    belirsiz: List[str]  # hiç hesaplanamayan öznitelikler (sensör eksik)

    @property
    def uniteler(self):
        return list(self.H["unit_id"].unique())

    def unite(self, uid):
        """Bir ünitenin (saatlik öznitelikler, tahminler) dilimi."""
        m = (self.H["unit_id"] == uid).to_numpy()
        return self.H[m], self.pred[m]

    def rapor_tablosu(self) -> pd.DataFrame:
        """Saatlik rapor (predict.py'nin yazdığı CSV ile aynı sütunlar)."""
        return self.H[["timestamp", "unit_id"]].join(self.pred.round(3))


def analiz_et(source, model, *, gauge: bool = False, setpoint: Optional[float] = None) -> Analiz:
    """`source`: dosya yolu ya da dosya benzeri nesne. Basınç/set değeri seçenekleri load_csv'ye gider."""
    raw = load_csv(source, gauge=gauge, setpoint=setpoint)
    H = hourly_features(raw)
    if H.empty:
        raise YetersizVeri("Öznitelik üretilemedi: veri çok kısa ya da çok boşluklu (en az ~1 gün gerekir).")
    pred = model.predict(H)
    belirsiz = [f for f in FEATURES if H[f].isna().all()]
    return Analiz(raw=raw, H=H, pred=pred, rapor=raw.attrs["rapor"], belirsiz=belirsiz)


def son_durum(pred: pd.DataFrame, H: pd.DataFrame, model) -> dict:
    """Bir ünitenin son saatteki durumu; `pred` ve `H` yalnızca o üniteye ait olmalı."""
    last = pred.iloc[-1]
    row = H.loc[last.name]
    d = dict(
        zaman=row["timestamp"],
        saat_sayisi=len(pred),
        health=float(last["health"]),
        status=last["status"],
        pred_fault=last["pred_fault"],
        confidence=float(last["confidence"]),
        eta_h=last["eta_h"],
        oneri=FAULTS[last["pred_fault"]]["oneri"],
        sapmalar=model.explain(row),
        uyari_baslangic=None,
        uyari_saat=0,
    )
    # Uyarı süresi: sondan geriye doğru kesintisiz Normal dışı saatler
    alarm = (pred["status"] != "Normal").to_numpy()
    if alarm[-1]:
        n = 0
        while n < len(alarm) and alarm[-1 - n]:
            n += 1
        d["uyari_baslangic"] = H.loc[pred.index[-n], "timestamp"]
        d["uyari_saat"] = n
    return d
