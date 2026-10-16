import numpy as np
import pandas as pd
import pytest

from backend.analysis import build_features, build_targets, evaluate_baseline


def test_features_and_target_use_only_current_and_next_close():
    closes = pd.Series(
        [100.0 + step for step in range(25)],
        index=pd.date_range("2025-01-01", periods=25, freq="D", tz="UTC"),
    )
    features = build_features(closes)
    targets = build_targets(closes)
    row = closes.index[20]

    assert features.loc[row, "return_1"] == pytest.approx(closes.loc[row] / closes.iloc[19] - 1)
    assert targets.loc[row] == 1
    assert pd.isna(targets.iloc[-1])


def test_baseline_uses_chronological_train_validation_test_splits():
    random = np.random.default_rng(17)
    returns = random.normal(0.0005, 0.01, 360)
    closes = 100 * np.cumprod(1 + returns)
    timestamps = pd.date_range("2024-01-01", periods=len(closes), freq="D", tz="UTC")

    report, _model = evaluate_baseline(closes, timestamps.astype(str))

    train_end = report["split"]["train"][1]
    validation_start = report["split"]["validation"][0]
    validation_end = report["split"]["validation"][1]
    test_start = report["split"]["test"][0]
    assert train_end < validation_start < validation_end < test_start
    assert report["label_end"]["train"] < validation_start
    assert report["label_end"]["validation"] < test_start
    assert report["confidence_probability"] is None


def test_baseline_refuses_short_history():
    with pytest.raises(ValueError, match="daily bars are required"):
        evaluate_baseline(np.linspace(100, 120, 100))