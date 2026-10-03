"""Ortak fixture'lar: küçük eğitim bir kez çalışır, geçici klasöre yazılır (repo'ya dokunmaz)."""

import joblib
import pytest

import train

# Hızlı ama anlamlı bir model için yeterli küçük filo
N_TRAIN, N_TEST, DAYS, N_ROBUST = 25, 8, 15, 14


@pytest.fixture(scope="session")
def trained_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("sogutma_root")
    train.main(n_train=N_TRAIN, n_test=N_TEST, days=DAYS, out_root=root, n_robust=N_ROBUST)
    return root


@pytest.fixture(scope="session")
def model(trained_root):
    return joblib.load(trained_root / "models" / "predictor.joblib")
