import inspect
import json

import pandas as pd

import train
from sogutma.faults import FAULT_TYPES

from .conftest import DAYS, N_TEST, N_TRAIN


def test_defaults_unchanged():
    p = inspect.signature(train.main).parameters
    assert (p["n_train"].default, p["n_test"].default, p["days"].default) == (120, 40, 30)
    assert p["out_root"].default == train.ROOT


def test_outputs_written_to_out_root(trained_root):
    for rel in ("data/demo_raw.parquet", "data/demo_hourly.parquet", "data/demo_units.json",
                "models/predictor.joblib", "models/metrics.json"):
        assert (trained_root / rel).exists(), rel
    # repo klasörüne yazılmamalı
    assert trained_root != train.ROOT


def test_metrics_content(trained_root):
    m = json.loads((trained_root / "models/metrics.json").read_text())
    assert (m["n_train_units"], m["n_test_units"], m["days"]) == (N_TRAIN, N_TEST, DAYS)
    assert 0.8 < m["accuracy"] <= 1
    assert m["labels"] == FAULT_TYPES
    assert len(m["confusion_matrix"]) == len(FAULT_TYPES)
    assert len(m["early_warning"]) == N_TEST
    assert set(m["per_class"]) == set(FAULT_TYPES)


def test_demo_data_consistent(trained_root):
    hourly = pd.read_parquet(trained_root / "data/demo_hourly.parquet")
    units = json.loads((trained_root / "data/demo_units.json").read_text())
    assert hourly["unit_id"].nunique() == len(units) == 8
    for c in ("health", "status", "pred_fault", "eta_h", "confidence"):
        assert c in hourly.columns
    assert units[0]["failure_h"] is not None
