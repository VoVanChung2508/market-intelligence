# AI Model Report

## Evaluated baseline

- Run date: 2026-10-16 UTC.
- Asset: AAPL daily history already stored locally.
- Target: next observed daily close direction (`next_close > current_close`). This is not a return forecast or profit probability.
- Observations: 502 bars; 479 usable labeled samples after rolling-window warm-up and purging split-boundary labels.
- Features available at decision time: one-day return, five-day return, 20-day return volatility, and price relative to 20-day mean.
- Models: class-weighted logistic regression with standardization; `DummyClassifier(strategy="prior")` majority/prior baseline.
- Split: chronological 60% train, 20% validation, 20% holdout test. No random shuffle. Samples whose next-close target crossed into the following split were purged.
- Version: `baseline-logistic-v2`.

## Results

| Split | Logistic accuracy | Baseline accuracy | Logistic balanced accuracy | Baseline balanced accuracy | Logistic Brier |
|---|---:|---:|---:|---:|---:|
| Validation | 47.37% | 55.79% | 47.39% | 50.00% | 0.251712 |
| Test | 52.58% | 54.64% | 52.94% | 50.00% | 0.248915 |

Required gate: beat the baseline by at least 2 percentage points on both accuracy and balanced accuracy in both validation and test. **Result: FAIL.** No direction is emitted and confidence probability is unset. The Brier score is included for monitoring only; the output probability has not been calibrated.

The current artifact and report are stored at `data/processed/models/baseline-logistic-v2/`. The previous `baseline-logistic-v1` artifact is retained as superseded history. These are local research artifacts, not a production model. The training command refuses to overwrite an existing version directory.

## Limitations

One asset and one historical period are insufficient to establish generalization. The dataset source/rights are inherited from local Yahoo Finance CSVs. No walk-forward refit, regime analysis, confidence calibration, drift monitoring, benchmark portfolio, transaction costs, or trading strategy test has been completed. A passing metric gate would still not guarantee future accuracy or returns.
