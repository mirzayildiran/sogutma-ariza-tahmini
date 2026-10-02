import pytest

from sogutma.roi import RoiGirdi, duyarlilik, hesapla

G = RoiGirdi(
    unite_sayisi=10, ariza_per_unite_yil=0.5, acil_onarim_tl=15000, urun_kaybi_tl=40000,
    erken_yakalama_orani=0.8, onarim_tasarruf_orani=0.4, urun_koruma_orani=0.8,
    sistem_aylik_tl_unite=300, kurulum_tl_unite=6000)


def _with(**kw):
    return RoiGirdi(**{**G.__dict__, **kw})


def test_known_numbers():
    s = hesapla(G)
    assert s.yillik_ariza == pytest.approx(5)
    assert s.yillik_yakalanan == pytest.approx(4)
    assert s.ariza_basi_kazanc_tl == pytest.approx(15000 * 0.4 + 40000 * 0.8)  # 38.000
    assert s.yillik_onlenen_tl == pytest.approx(152_000)
    assert s.yillik_sistem_tl == pytest.approx(36_000)
    assert s.yillik_net_tl == pytest.approx(116_000)
    assert s.kurulum_tl == pytest.approx(60_000)
    assert s.geri_odeme_ay == pytest.approx(60_000 / (116_000 / 12))
    assert s.basabas_yakalama_orani == pytest.approx(36_000 / (5 * 38_000))


def test_cumulative_three_years():
    s = hesapla(G)
    assert len(s.kumulatif_tl) == 37
    assert s.kumulatif_tl[0] == pytest.approx(-60_000)
    assert s.kumulatif_tl[12] == pytest.approx(-60_000 + 116_000)
    assert s.kumulatif_tl[36] == pytest.approx(-60_000 + 3 * 116_000)
    # Geri ödeme anında kümülatif net sıfırdır
    assert -60_000 + 116_000 / 12 * s.geri_odeme_ay == pytest.approx(0, abs=1e-6)


def test_no_payback_when_net_not_positive():
    s = hesapla(_with(erken_yakalama_orani=0.1))  # önlenen 19.000 < sistem 36.000
    assert s.yillik_net_tl < 0 and s.geri_odeme_ay is None
    assert s.kumulatif_tl[-1] < s.kumulatif_tl[0]
    sifir = hesapla(_with(erken_yakalama_orani=0.0))
    assert sifir.yillik_net_tl == pytest.approx(-36_000) and sifir.geri_odeme_ay is None


def test_zero_install_pays_back_immediately():
    assert hesapla(_with(kurulum_tl_unite=0)).geri_odeme_ay == 0


def test_breakeven_rate_is_where_net_is_zero():
    s = hesapla(G)
    at_break = hesapla(_with(erken_yakalama_orani=s.basabas_yakalama_orani))
    assert at_break.yillik_net_tl == pytest.approx(0, abs=1e-6)
    # Önlenecek maliyet yoksa başabaş tanımsız
    assert hesapla(_with(urun_kaybi_tl=0, acil_onarim_tl=0)).basabas_yakalama_orani is None


def test_sensitivity_monotonic_and_matches_point():
    rates = [0, 0.25, 0.5, 0.8, 1.0]
    net = duyarlilik(G, rates)
    assert net == sorted(net)
    assert net[0] == pytest.approx(-36_000)
    assert net[3] == pytest.approx(hesapla(G).yillik_net_tl)
