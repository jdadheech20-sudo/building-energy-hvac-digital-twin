from __future__ import annotations

from datetime import timedelta, timezone

import numpy as np
import pandas as pd

from .forecasting import metric_report
from .models import ReadingRecord


def as_utc(value):
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def forecast_from_readings(building_id: str, meter_type: str, readings: list[ReadingRecord], horizon: int = 24):
    if not 1 <= horizon <= 168:
        raise ValueError("horizon must be between 1 and 168 intervals")
    ordered = sorted(readings, key=lambda item: item.interval_end)
    if len(ordered) < 25:
        raise ValueError("at least 25 labeled intervals are required for a daily baseline")
    durations = {(item.interval_end - item.interval_start).total_seconds() for item in ordered[-25:]}
    if len(durations) != 1 or next(iter(durations)) <= 0:
        raise ValueError("baseline forecast requires a constant documented interval")
    interval_seconds = next(iter(durations))
    interval = timedelta(seconds=interval_seconds)
    values = np.asarray([item.value for item in ordered], dtype=float)
    if np.any(~np.isfinite(values)) or np.any(values < 0):
        raise ValueError("forecast source contains invalid values")
    lag = int(7 * 24 * 3600 / interval_seconds) if len(ordered) >= int(7 * 24 * 3600 / interval_seconds) + 1 else int(24 * 3600 / interval_seconds)
    if lag <= 0 or len(values) <= lag:
        raise ValueError("not enough history for the selected seasonal baseline")
    backtest = values[lag:]
    backtest_pred = values[:-lag]
    residuals = np.abs(backtest - backtest_pred)
    radius = float(np.quantile(residuals, 0.95, method="higher")) if len(residuals) >= 12 else float(np.max(residuals))
    predictions = [float(values[-lag + (i % lag)]) for i in range(horizon)]
    last = as_utc(ordered[-1].interval_end)
    status = ordered[-1].evidence_status
    points = []
    for i, value in enumerate(predictions, start=1):
        stamp = last + i * interval
        points.append({"timestamp": stamp, "value": value, "lower": max(0.0, value - radius), "upper": value + radius, "meter_type": meter_type, "unit": ordered[-1].unit, "evidence_status": "predicted"})
    metrics = metric_report(backtest, backtest_pred)
    return {
        "building_id": building_id,
        "model_name": "daily-seasonal-baseline" if lag * interval_seconds == 24 * 3600 else "weekly-persistence-baseline",
        "source_evidence_status": status,
        "method": f"repeat previous {lag} source intervals; empirical 95th percentile absolute residual interval",
        "interval_hours": round(interval_seconds / 3600),
        "metrics": metrics,
        "points": points,
        "limitations": ["Baseline demonstration only; not an ASHRAE benchmark result.", "Prediction uncertainty is a simple empirical residual interval and is not a deployment guarantee."],
    }


def latest_robust_anomaly(readings: list[ReadingRecord], *, min_history: int = 24, threshold: float = 4.5):
    ordered = sorted(readings, key=lambda item: item.interval_end)
    if len(ordered) < min_history + 1:
        return {"flagged": False, "status": "insufficient_history", "robust_z": None}
    history = np.asarray([item.value for item in ordered[-min_history - 1 : -1]], dtype=float)
    latest = float(ordered[-1].value)
    median = float(np.median(history))
    mad = float(np.median(np.abs(history - median)))
    scale = max(1.4826 * mad, 1e-9)
    score = (latest - median) / scale
    return {"flagged": bool(abs(score) > threshold), "status": "candidate_anomaly_only", "robust_z": float(score), "method": "median/MAD against prior intervals", "reading_id": ordered[-1].id}
