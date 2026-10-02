"""Maliyet ve kazanç (ROI) hesabı: saf fonksiyonlar, panel sayfası bunları çağırır.

Model basit ve açıktır; her sayı kullanıcıdan gelir. Bu modül hiçbir piyasa verisi
varsaymaz: panelde gösterilen varsayılanlar yalnızca örnek değerdir.

Mantık: bir arıza erken yakalanırsa acil onarımın bir kısmı (planlı onarım ucuzdur) ve
ürün kaybının bir kısmı önlenir. Önlenen yıllık maliyet bunların toplamıdır; sistem
maliyeti ünite başına aylık ücret ile tek seferlik kurulumdan oluşur.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import List, Optional

AY_SAYISI = 12


@dataclass(frozen=True)
class RoiGirdi:
    unite_sayisi: float
    ariza_per_unite_yil: float  # ünite başına yılda beklenen plansız arıza
    acil_onarim_tl: float  # bir acil onarımın ortalama maliyeti
    urun_kaybi_tl: float  # bir arızada ortalama ürün kaybı
    erken_yakalama_orani: float  # 0–1: arızaların erken yakalanan payı
    onarim_tasarruf_orani: float  # 0–1: erken yakalanınca onarım maliyetinden tasarruf (planlı / acil)
    urun_koruma_orani: float  # 0–1: erken yakalanınca ürün kaybının önlenen payı
    sistem_aylik_tl_unite: float  # ünite başına aylık sistem maliyeti
    kurulum_tl_unite: float  # ünite başına tek seferlik kurulum maliyeti


@dataclass(frozen=True)
class RoiSonuc:
    yillik_ariza: float  # filo genelinde beklenen yıllık plansız arıza
    yillik_yakalanan: float  # bunlardan erken yakalananlar
    ariza_basi_kazanc_tl: float  # erken yakalanan bir arızada önlenen maliyet
    yillik_onlenen_tl: float
    yillik_sistem_tl: float
    yillik_net_tl: float
    kurulum_tl: float
    geri_odeme_ay: Optional[float]  # None: net fayda ≤ 0, geri ödeme yok
    kumulatif_tl: List[float]  # 0. aydan başlayarak aylık kümülatif net (kurulum dahil)
    basabas_yakalama_orani: Optional[float]  # net faydanın 0 olduğu yakalama oranı; None: ulaşılamaz


def hesapla(g: RoiGirdi, ay: int = 36) -> RoiSonuc:
    yillik_ariza = g.unite_sayisi * g.ariza_per_unite_yil
    yakalanan = yillik_ariza * g.erken_yakalama_orani
    kazanc = g.acil_onarim_tl * g.onarim_tasarruf_orani + g.urun_kaybi_tl * g.urun_koruma_orani
    onlenen = yakalanan * kazanc
    sistem = g.unite_sayisi * g.sistem_aylik_tl_unite * AY_SAYISI
    net = onlenen - sistem
    kurulum = g.unite_sayisi * g.kurulum_tl_unite

    if net > 0:
        geri_odeme = kurulum / (net / AY_SAYISI)
    else:
        geri_odeme = None
    aylik_net = net / AY_SAYISI
    kumulatif = [-kurulum + aylik_net * m for m in range(ay + 1)]

    tam_kazanc = yillik_ariza * kazanc  # yakalama oranı %100 iken önlenen
    basabas = sistem / tam_kazanc if tam_kazanc > 0 else None
    return RoiSonuc(
        yillik_ariza=yillik_ariza,
        yillik_yakalanan=yakalanan,
        ariza_basi_kazanc_tl=kazanc,
        yillik_onlenen_tl=onlenen,
        yillik_sistem_tl=sistem,
        yillik_net_tl=net,
        kurulum_tl=kurulum,
        geri_odeme_ay=geri_odeme,
        kumulatif_tl=kumulatif,
        basabas_yakalama_orani=basabas,
    )


def duyarlilik(g: RoiGirdi, oranlar) -> List[float]:
    """Erken yakalama oranına göre yıllık net faydalar (diğer girdiler sabit)."""
    return [hesapla(replace(g, erken_yakalama_orani=float(r)), ay=0).yillik_net_tl for r in oranlar]
