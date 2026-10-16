from collections.abc import Sequence
from math import floor

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score, brier_score_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


MODEL_VERSION = "baseline-logistic-v2"
FEATURE_COLUMNS = ("return_1", "return_5", "volatility_20", "trend_20")
MINIMUM_DAILY_BARS = 150
MINIMUM_BASELINE_LIFT = 0.02


def build_features(closes: pd.Series) -> pd.DataFrame:
    prices = pd.to_numeric(closes, errors="coerce").astype(float)
    returns = prices.pct_change(fill_method=None)
    features = pd.DataFrame(index=prices.index)
    features["return_1"] = returns
    features["return_5"] = prices.pct_change(5, fill_method=None)
    features["volatility_20"] = returns.rolling(20, min_periods=20).std()
    features["trend_20"] = prices / prices.rolling(20, min_periods=20).mean() - 1
    return features


def build_targets(closes: pd.Series) -> pd.Series:
    prices = pd.to_numeric(closes, errors="coerce").astype(float)
    next_close = prices.shift(-1)
    return (next_close > prices).where(next_close.notna()).astype("Int64")


def _metrics(labels: pd.Series, predictions: np.ndarray, probabilities: np.ndarray) -> dict[str, float]:
    return {
        "accuracy": round(float(accuracy_score(labels, predictions)), 6),
        "balanced_accuracy": round(
            float(balanced_accuracy_score(labels, predictions)), 6
        ),
        "brier_score": round(float(brier_score_loss(labels, probabilities)), 6),
    }


def evaluate_baseline(
    closes: Sequence[float] | pd.Series,
    timestamps: Sequence[str] | None = None,
) -> tuple[dict[str, object], object]:
    prices = pd.Series(closes, dtype="float64")
    if timestamps is not None:
        prices.index = pd.to_datetime(list(timestamps), utc=True)
    prices = prices.dropna().sort_index()
    prices = prices[prices > 0]
    if len(prices) < MINIMUM_DAILY_BARS:
        raise ValueError(
            f"At least {MINIMUM_DAILY_BARS} daily bars are required; got {len(prices)}"
        )

    features = build_features(prices)
    targets = build_targets(prices)
    target_timestamps = pd.Series(prices.index, index=prices.index).shift(-1)
    labeled = (
        features.join(targets.rename("target"))
        .join(target_timestamps.rename("target_timestamp"))
        .dropna()
    )
    labeled["target"] = labeled["target"].astype(int)
    available_samples = len(labeled)
    train_end = floor(available_samples * 0.6)
    validation_end = floor(available_samples * 0.8)
    if train_end < 2 or validation_end <= train_end or available_samples <= validation_end:
        raise ValueError("Insufficient observations for chronological splits")

    train = labeled.iloc[:train_end]
    validation = labeled.iloc[train_end:validation_end]
    test = labeled.iloc[validation_end:]
    train = train[train["target_timestamp"] < validation.index[0]]
    validation = validation[validation["target_timestamp"] < test.index[0]]
    sample_count = len(train) + len(validation) + len(test)
    if train.empty or validation.empty or test.empty:
        raise ValueError("Insufficient observations after purging split boundaries")
    if train["target"].nunique() < 2:
        raise ValueError("Training period contains only one target class")

    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(class_weight="balanced", max_iter=500, random_state=42),
    )
    baseline = DummyClassifier(strategy="prior")
    model.fit(train[list(FEATURE_COLUMNS)], train["target"])
    baseline.fit(train[list(FEATURE_COLUMNS)], train["target"])

    reports: dict[str, dict[str, float]] = {}
    baseline_reports: dict[str, dict[str, float]] = {}
    for split_name, split in (("validation", validation), ("test", test)):
        split_features = split[list(FEATURE_COLUMNS)]
        labels = split["target"]
        model_probabilities = model.predict_proba(split_features)[:, 1]
        baseline_probabilities = baseline.predict_proba(split_features)[:, 1]
        reports[split_name] = _metrics(
            labels,
            model.predict(split_features),
            model_probabilities,
        )
        baseline_reports[split_name] = _metrics(
            labels,
            baseline.predict(split_features),
            baseline_probabilities,
        )

    gate_passed = all(
        reports[split][metric]
        >= baseline_reports[split][metric] + MINIMUM_BASELINE_LIFT
        for split in ("validation", "test")
        for metric in ("accuracy", "balanced_accuracy")
    )

    latest_features = features.dropna().iloc[[-1]][list(FEATURE_COLUMNS)]
    latest_direction = None
    if gate_passed and not latest_features.empty:
        latest_direction = "up" if int(model.predict(latest_features)[0]) == 1 else "down"

    report: dict[str, object] = {
        "model_version": MODEL_VERSION,
        "problem": "next_daily_close_direction",
        "observations": len(prices),
        "labeled_samples": sample_count,
        "split": {
            "train": [str(train.index[0]), str(train.index[-1])],
            "validation": [str(validation.index[0]), str(validation.index[-1])],
            "test": [str(test.index[0]), str(test.index[-1])],
        },
        "label_end": {
            "train": str(train["target_timestamp"].iloc[-1]),
            "validation": str(validation["target_timestamp"].iloc[-1]),
            "test": str(test["target_timestamp"].iloc[-1]),
        },
        "features": list(FEATURE_COLUMNS),
        "metrics": reports,
        "majority_baseline": baseline_reports,
        "minimum_required_lift": MINIMUM_BASELINE_LIFT,
        "gate_passed": gate_passed,
        "latest_direction": latest_direction,
        "confidence_probability": None,
    }
    return report, model