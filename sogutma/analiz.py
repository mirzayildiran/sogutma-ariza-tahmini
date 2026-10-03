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
from .ingest import IngestReport, load_csv, load_dataframe
from .veri_kalitesi import KaliteRaporu, degerlendir


class YetersizVeri(ValueError):
    """Dosya geçerli ama saatlik öznitelik üretmek için çok kısa ya da çok boşluklu."""


@dataclass
class Analiz:
    raw: pd.DataFrame  # 5 dakikalık, doğrulanmış ham veri
    H: pd.DataFrame  # saatlik öznitelikler
    pred: pd.DataFrame  # model çıktısı (H ile aynı indeks)
    rapor: IngestReport
    belirsiz: List[str]  # hiç hesaplanamayan öznitelikler (sensör eksik)
    kalite: Optional[KaliteRaporu] = None  # sensör sağlığı kontrolü (kapalıysa None)

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

    def sensor_sorunlari(self, uid=None) -> pd.DataFrame:
        """Sensör sorunlarını ünite/sensör/neden bazında özetler."""
        kolonlar = ["unit_id", "sensor", "neden", "ilk", "son", "saat"]
        if self.kalite is None:
            return pd.DataFrame(columns=kolonlar)
        sorunlar = self.kalite.sorunlar()
        return sorunlar if uid is None else sorunlar[sorunlar["unit_id"] == uid].reset_index(drop=True)


def ham_tahmin(raw: pd.DataFrame, model, *, sensor_kontrolu: bool = True):
    """Ham 5 dakikalık veriden öznitelik, tahmin ve kalite raporu üretir.

    Aynı akış CSV, JSON API, panel, CLI ve sentetik değerlendirme tarafından kullanılır.
    """
    if not sensor_kontrolu:
        H = hourly_features(raw)
        return H, (model.predict(H) if not H.empty else None), None, H
    kalite = degerlendir(raw)
    H_ham = hourly_features(kalite.temiz)
    if H_ham.empty:
        return H_ham, None, kalite, H_ham
    H, notlar = kalite.uygula(H_ham)
    return H, model.predict(H).join(notlar), kalite, H_ham


def _analiz(raw: pd.DataFrame, model, *, sensor_kontrolu: bool = True) -> Analiz:
    H, pred, kalite, H_ham = ham_tahmin(raw, model, sensor_kontrolu=sensor_kontrolu)
    if H.empty:
        raise YetersizVeri("Öznitelik üretilemedi: veri çok kısa ya da çok boşluklu (en az ~1 gün gerekir).")
    belirsiz = [f for f in FEATURES if H_ham[f].isna().all()]
    return Analiz(raw=raw, H=H, pred=pred, rapor=raw.attrs["rapor"], belirsiz=belirsiz, kalite=kalite)


def analiz_et(source, model, *, gauge: bool = False, setpoint: Optional[float] = None,
              tip: Optional[str] = None, sensor_kontrolu: bool = True) -> Analiz:
    """`source`: dosya yolu ya da dosya benzeri nesne. Basınç/set değeri/tip seçenekleri load_csv'ye gider."""
    return _analiz(load_csv(source, gauge=gauge, setpoint=setpoint, tip=tip,
                            allow_sensor_faults=sensor_kontrolu), model,
                    sensor_kontrolu=sensor_kontrolu)


def analiz_et_tablo(df: pd.DataFrame, model, *, gauge: bool = False, setpoint: Optional[float] = None,
                    tip: Optional[str] = None, sensor_kontrolu: bool = True) -> Analiz:
    """`analiz_et` ile aynı, ama metin sütunlu hazır tablodan (ör. API'nin JSON ölçümlerinden)."""
    return _analiz(load_dataframe(df, gauge=gauge, setpoint=setpoint, tip=tip,
                                  allow_sensor_faults=sensor_kontrolu), model,
                    sensor_kontrolu=sensor_kontrolu)


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
        sensor_sorunu=bool(last.get("sensor_sorunu", False)),
        sensor_notu=str(last.get("sensor_notu", "") or ""),
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
