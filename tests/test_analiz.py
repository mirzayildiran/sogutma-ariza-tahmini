from pathlib import Path

import pytest

from sogutma.analiz import analiz_et, son_durum
from sogutma.ingest import ValidationError

ORNEK = Path(__file__).parent.parent / "examples" / "ornek_veri.csv"


def test_analiz_example(model):
    a = analiz_et(ORNEK, model)
    assert a.uniteler == ["U1"] and a.belirsiz == []
    assert len(a.pred) == len(a.H)
    rep = a.rapor_tablosu()
    assert list(rep.columns[:2]) == ["timestamp", "unit_id"]
    assert {"health", "status", "pred_fault", "eta_h"} <= set(rep.columns)
    H, p = a.unite("U1")
    d = son_durum(p, H, model)
    assert 0 <= d["health"] <= 100 and d["status"] in ("Normal", "İzlemede", "Kritik")
    assert d["saat_sayisi"] == len(p)
    assert (d["uyari_saat"] > 0) == (d["status"] != "Normal")


def test_analiz_validation_error(model, tmp_path):
    bad = tmp_path / "bozuk.csv"
    bad.write_text("a;b\n1;2\n", encoding="utf-8")
    with pytest.raises(ValidationError) as e:
        analiz_et(bad, model)
    assert "Zorunlu" in str(e.value) and e.value.hatalar


def test_analiz_short_file_rejected(model, tmp_path):
    satirlar = ORNEK.read_text(encoding="utf-8-sig").splitlines()[: 1 + 12 * 12]  # yalnızca 12 saat
    kisa = tmp_path / "kisa.csv"
    kisa.write_text("\n".join(satirlar), encoding="utf-8")
    with pytest.raises(ValidationError) as e:
        analiz_et(kisa, model)
    assert "en az" in str(e.value)
