from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .units import unit_is_supported

ASHRAE_METERS = {0: "electricity", 1: "chilled_water", 2: "steam", 3: "hot_water"}
ASHRAE_COLUMNS = {"building_id", "meter", "timestamp", "meter_reading"}
REQUIRED_MODEL_COLUMNS = {"building_id", "meter_type", "timestamp", "value", "unit"}


@dataclass(frozen=True)
class TimeSplit:
    train_end: datetime
    calibration_end: datetime
    test_start: datetime


def load_ashrae_training_data(
    root: str | Path,
    *,
    site_timezones: dict[int, str],
    meter_units: dict[str, str],
) -> pd.DataFrame:
    """Load the labeled ASHRAE competition train split with explicit unit/timezone maps."""
    root = Path(root)
    files = {name: root / name for name in ("train.csv", "building_metadata.csv", "weather_train.csv")}
    missing = [str(path) for path in files.values() if not path.exists()]
    if missing:
        raise FileNotFoundError("missing ASHRAE files: " + ", ".join(missing))
    absent_meter_units = sorted(set(ASHRAE_METERS.values()) - set(meter_units))
    if absent_meter_units:
        raise ValueError("explicit meter units are required for: " + ", ".join(absent_meter_units))

    meta = pd.read_csv(files["building_metadata.csv"])
    train = pd.read_csv(files["train.csv"], usecols=["building_id", "meter", "timestamp", "meter_reading"])
    weather = pd.read_csv(files["weather_train.csv"])
    if "site_id" not in meta or "site_id" not in weather:
        raise ValueError("building metadata and weather must provide site_id")
    train["timestamp"] = pd.to_datetime(train.timestamp, errors="raise")
    weather["timestamp"] = pd.to_datetime(weather.timestamp, errors="raise")
    if train.timestamp.dt.tz is not None or weather.timestamp.dt.tz is not None:
        raise ValueError("expected source-local ASHRAE timestamps; provide a documented local-time import")
    train = train.merge(meta[["building_id", "site_id", "primary_use", "square_feet", "year_built"]], on="building_id", how="left", validate="many_to_one")
    sites = sorted(int(x) for x in train.site_id.dropna().unique())
    if set(sites) - set(site_timezones):
        raise ValueError("explicit timezone missing for site ids: " + ", ".join(map(str, sorted(set(sites) - set(site_timezones)))))
    if train.site_id.isna().any():
        raise ValueError("building metadata has missing site_id values")
    if not set(train.meter.unique()).issubset(ASHRAE_METERS):
        raise ValueError("train.csv contains an unsupported meter code")
    train["meter_type"] = train.meter.map(ASHRAE_METERS)
    if train.meter_reading.isna().any() or (train.meter_reading < 0).any():
        raise ValueError("meter_reading contains missing or negative values")
    train["unit"] = train.meter_type.map(meter_units)

    frames = []
    for site_id, part in train.groupby("site_id", sort=True):
        zone = ZoneInfo(site_timezones[int(site_id)])
        local = part.timestamp.dt.tz_localize(zone, ambiguous="raise", nonexistent="raise")
        part = part.copy()
        part["timestamp"] = local.dt.tz_convert("UTC")
        frames.append(part)
    train = pd.concat(frames, ignore_index=True)
    weather_frames = []
    for site_id, part in weather.groupby("site_id", sort=True):
        if int(site_id) not in site_timezones:
            raise ValueError(f"explicit timezone missing for weather site {int(site_id)}")
        part = part.copy()
        part["timestamp"] = part.timestamp.dt.tz_localize(
            ZoneInfo(site_timezones[int(site_id)]), ambiguous="raise", nonexistent="raise"
        ).dt.tz_convert("UTC")
        weather_frames.append(part)
    weather = pd.concat(weather_frames, ignore_index=True)
    weather_columns = [c for c in ["site_id", "timestamp", "air_temperature", "dew_temperature", "relative_humidity", "wind_speed", "cloud_coverage"] if c in weather]
    train = train.merge(weather[weather_columns], on=["site_id", "timestamp"], how="left", validate="many_to_one")
    train = train.rename(columns={"meter_reading": "value"})
    return train.sort_values(["building_id", "meter_type", "timestamp"]).reset_index(drop=True)


def validate_model_frame(frame: pd.DataFrame) -> list[str]:
    errors = []
    missing = sorted(REQUIRED_MODEL_COLUMNS - set(frame.columns))
    if missing:
        return ["Missing columns: " + ", ".join(missing)]
    parsed_timestamps = []
    has_invalid_timestamp = False
    has_naive_timestamp = False
    for value in frame.timestamp:
        try:
            stamp = pd.Timestamp(value)
        except (TypeError, ValueError, OverflowError):
            has_invalid_timestamp = True
            continue
        if pd.isna(stamp):
            has_invalid_timestamp = True
        elif stamp.tzinfo is None or stamp.utcoffset() is None:
            has_naive_timestamp = True
        else:
            parsed_timestamps.append(stamp)
    if has_invalid_timestamp:
        errors.append("timestamp contains invalid values")
    if has_naive_timestamp:
        errors.append("timestamp must be timezone-aware before normalization")
    timestamps = pd.to_datetime(frame.timestamp, errors="coerce", utc=True)
    if frame[["building_id", "meter_type", "timestamp"]].duplicated().any():
        errors.append("building_id, meter_type, timestamp must be unique")
    values = pd.to_numeric(frame.value, errors="coerce")
    if values.isna().any() or not np.isfinite(values.to_numpy()).all():
        errors.append("value must be finite numeric data")
    if (values < 0).any():
        errors.append("meter values must be nonnegative")
    if frame.unit.isna().any() or frame.unit.astype(str).str.strip().eq("").any():
        errors.append("unit is required for every reading")
    unsupported_units = sorted({
        (str(meter), str(unit).strip())
        for meter, unit in zip(frame.meter_type, frame.unit)
        if pd.notna(unit) and str(unit).strip() and not unit_is_supported(str(meter), str(unit).strip())
    })
    if unsupported_units:
        errors.append("unsupported meter type/unit pairs: " + ", ".join(f"{meter}={unit}" for meter, unit in unsupported_units))
    return errors


def make_supervised(frame: pd.DataFrame, *, daily_lag: int = 24, weekly_lag: int = 168) -> pd.DataFrame:
    errors = validate_model_frame(frame)
    if errors:
        raise ValueError("; ".join(errors))
    d = frame.copy().sort_values(["building_id", "meter_type", "timestamp"])
    group_keys = ["building_id", "meter_type"]
    for lag_hours, column in ((daily_lag, "lag_24"), (weekly_lag, "lag_168")):
        lagged = d[group_keys + ["timestamp", "value"]].copy()
        lagged["timestamp"] = lagged.timestamp + pd.Timedelta(hours=lag_hours)
        lagged = lagged.rename(columns={"value": column})
        d = d.merge(lagged, on=group_keys + ["timestamp"], how="left", validate="one_to_one")
    ts = d.timestamp.dt
    d["hour"] = ts.hour
    d["day_of_week"] = ts.day_of_week
    d["month"] = ts.month
    d["is_weekend"] = (ts.day_of_week >= 5).astype(int)
    d["hour_sin"] = np.sin(2 * np.pi * d.hour / 24)
    d["hour_cos"] = np.cos(2 * np.pi * d.hour / 24)
    d["day_sin"] = np.sin(2 * np.pi * d.day_of_week / 7)
    d["day_cos"] = np.cos(2 * np.pi * d.day_of_week / 7)
    return d


def metric_report(observed, predicted) -> dict[str, float]:
    obs = np.asarray(observed, dtype=float)
    pred = np.asarray(predicted, dtype=float)
    if len(obs) != len(pred) or not len(obs):
        raise ValueError("observations and predictions must have equal nonzero length")
    err = pred - obs
    mean = float(np.mean(obs))
    rmse = float(np.sqrt(np.mean(err**2)))
    return {
        "n": int(len(obs)),
        "mae": float(np.mean(np.abs(err))),
        "rmse": rmse,
        "cvrmse_percent": float(100 * rmse / max(abs(mean), 1e-9)),
        "nmbe_percent": float(100 * np.mean(err) / max(abs(mean), 1e-9)),
    }


def persistence_predictions(frame: pd.DataFrame, *, seasonal_lag: int = 168) -> dict[str, np.ndarray]:
    seasonal = frame.lag_168.to_numpy(float)
    daily = frame.lag_24.to_numpy(float)
    fallback = np.where(np.isfinite(seasonal), seasonal, daily)
    if not np.isfinite(fallback).all():
        raise ValueError("history is insufficient for daily or weekly persistence")
    return {"weekly_persistence": fallback, "daily_seasonal": daily}


def split_chronologically(frame: pd.DataFrame, split: TimeSplit):
    if not (split.train_end < split.calibration_end < split.test_start):
        raise ValueError("split boundaries must be strictly chronological")
    ts = frame.timestamp
    train = frame.loc[ts <= split.train_end].copy()
    calibration = frame.loc[(ts > split.train_end) & (ts < split.test_start) & (ts <= split.calibration_end)].copy()
    test = frame.loc[ts >= split.test_start].copy()
    if min(len(train), len(calibration), len(test)) == 0:
        raise ValueError("train, calibration, and test partitions must all contain data")
    return train, calibration, test


def split_unseen_buildings(frame: pd.DataFrame, *, heldout_building_ids: set[Any]):
    known = frame.loc[~frame.building_id.isin(heldout_building_ids)].copy()
    unseen = frame.loc[frame.building_id.isin(heldout_building_ids)].copy()
    if not len(known) or not len(unseen):
        raise ValueError("both known-building and held-out-building partitions are required")
    return known, unseen


def fit_xgboost(train: pd.DataFrame, calibration: pd.DataFrame, test: pd.DataFrame, *, feature_columns: list[str], n_estimators: int = 350):
    """Fit one pooled model for one meter type and conformalize on a later split."""
    if train.meter_type.nunique() != 1 or calibration.meter_type.nunique() != 1 or test.meter_type.nunique() != 1:
        raise ValueError("fit_xgboost accepts one meter type per call")
    if not (train.meter_type.iloc[0] == calibration.meter_type.iloc[0] == test.meter_type.iloc[0]):
        raise ValueError("meter type must match across partitions")
    try:
        from xgboost import XGBRegressor
    except ImportError as exc:
        raise RuntimeError("install the optional 'ml' extra to use pooled XGBoost") from exc
    model = XGBRegressor(
        n_estimators=n_estimators,
        max_depth=7,
        learning_rate=0.04,
        subsample=0.85,
        colsample_bytree=0.85,
        objective="reg:squarederror",
        random_state=42,
        n_jobs=4,
    )
    model.fit(train[feature_columns], train.value)
    cal_pred = np.maximum(model.predict(calibration[feature_columns]), 0)
    test_pred = np.maximum(model.predict(test[feature_columns]), 0)
    radius = float(np.quantile(np.abs(calibration.value.to_numpy() - cal_pred), 0.95, method="higher"))
    report = metric_report(test.value, test_pred)
    report.update(
        interval_coverage=float(np.mean((test.value >= np.maximum(test_pred - radius, 0)) & (test.value <= test_pred + radius))),
        mean_interval_width=float(np.mean(test_pred + radius - np.maximum(test_pred - radius, 0))),
        calibration_radius_95=radius,
    )
    return model, test_pred, report


BASE_FEATURES = [
    "hour_sin", "hour_cos", "day_sin", "day_cos", "month",
    "square_feet", "year_built", "air_temperature", "dew_temperature",
    "relative_humidity", "wind_speed",
]


def feature_columns_for_history(history_days: int) -> list[str]:
    if history_days < 0:
        raise ValueError("history_days cannot be negative")
    columns = list(BASE_FEATURES)
    if history_days >= 1:
        columns.append("lag_24")
    if history_days >= 7:
        columns.append("lag_168")
    return columns


def run_ashrae_benchmark(frame: pd.DataFrame, *, train_end: datetime, calibration_end: datetime, test_start: datetime, heldout_building_ids: set[Any], n_estimators: int = 350) -> dict[str, Any]:
    """Evaluate per-meter pooled XGBoost, persistence, temporal, transfer, and history regimes."""
    missing_features = sorted(set(BASE_FEATURES) - set(frame.columns))
    if missing_features:
        raise ValueError("missing explicit weather/building features: " + ", ".join(missing_features))
    supervised = make_supervised(frame)
    known, unseen = split_unseen_buildings(supervised, heldout_building_ids=heldout_building_ids)
    split = TimeSplit(train_end=train_end, calibration_end=calibration_end, test_start=test_start)
    results: dict[str, Any] = {
        "scientific_status": "benchmark protocol only; source rows are labeled measured ASHRAE data",
        "splits": {
            "train_end": train_end.isoformat(),
            "calibration_end": calibration_end.isoformat(),
            "test_start": test_start.isoformat(),
            "heldout_building_ids": sorted(str(x) for x in heldout_building_ids),
        },
        "per_meter": {},
    }
    for meter_type in sorted(supervised.meter_type.unique()):
        meter_known = known.loc[known.meter_type == meter_type].copy()
        meter_unseen = unseen.loc[unseen.meter_type == meter_type].copy()
        section: dict[str, Any] = {"unit": str(meter_known.unit.iloc[0]) if len(meter_known) else None}
        temporal_rows = None
        temporal_sets = None
        try:
            temporal_rows = split_chronologically(meter_known, split)
            temporal_sets = []
            for part in temporal_rows:
                clean = part.dropna(subset=BASE_FEATURES + ["lag_24", "lag_168", "value"])
                temporal_sets.append(clean)
            train, calibration, test = temporal_sets
            if min(len(train), len(calibration), len(test)) < 30:
                raise ValueError("fewer than 30 complete rows in a temporal partition")
            columns = BASE_FEATURES + ["lag_24", "lag_168"]
            baseline = persistence_predictions(test)
            _, xgb_pred, xgb_report = fit_xgboost(train, calibration, test, feature_columns=columns, n_estimators=n_estimators)
            section["future_time"] = {
                "train_rows": len(train),
                "calibration_rows": len(calibration),
                "test_rows": len(test),
                "weekly_persistence": metric_report(test.value, baseline["weekly_persistence"]),
                "daily_seasonal": metric_report(test.value, baseline["daily_seasonal"]),
                "pooled_xgboost": xgb_report,
            }
        except (ValueError, RuntimeError) as exc:
            section["future_time"] = {"status": "not_evaluated", "reason": str(exc)}

        if temporal_rows is not None and len(meter_unseen):
            train_known, calibration_known, _ = temporal_rows
            history_outputs = {}
            for days in (0, 1, 7):
                columns = feature_columns_for_history(days)
                train_clean = train_known.dropna(subset=columns + ["value"])
                calibration_clean = calibration_known.dropna(subset=columns + ["value"])
                unseen_test = meter_unseen.loc[meter_unseen.timestamp >= test_start].dropna(subset=columns + ["value"])
                try:
                    if min(len(train_clean), len(calibration_clean), len(unseen_test)) < 30:
                        raise ValueError("fewer than 30 complete rows in a transfer partition")
                    _, pred, report = fit_xgboost(
                        train_clean, calibration_clean, unseen_test,
                        feature_columns=columns, n_estimators=n_estimators,
                    )
                    history_outputs[f"{days}_days"] = {
                        "train_rows": len(train_clean),
                        "calibration_rows": len(calibration_clean),
                        "test_rows": len(unseen_test),
                        "heldout_buildings": sorted(str(x) for x in unseen_test.building_id.unique()),
                        "pooled_xgboost": report,
                        "persistence": None if days == 0 else metric_report(
                            unseen_test.value,
                            unseen_test.lag_24.to_numpy(float) if days == 1 else unseen_test.lag_168.to_numpy(float),
                        ),
                    }
                except (ValueError, RuntimeError) as exc:
                    history_outputs[f"{days}_days"] = {"status": "not_evaluated", "reason": str(exc)}
            section["heldout_buildings_limited_history"] = history_outputs
        else:
            section["heldout_buildings_limited_history"] = {"status": "not_evaluated", "reason": "held-out meter rows or known-building time partitions are unavailable"}
        results["per_meter"][meter_type] = section
    return results


def robust_residual_flags(observed, predicted, *, min_history: int = 24, threshold: float = 4.5):
    obs = np.asarray(observed, dtype=float)
    pred = np.asarray(predicted, dtype=float)
    residuals = obs - pred
    if len(residuals) < min_history:
        return {"flags": np.zeros(len(residuals), dtype=bool), "robust_z": np.zeros(len(residuals)), "status": "insufficient_history"}
    median = float(np.median(residuals))
    mad = float(np.median(np.abs(residuals - median)))
    scale = max(1.4826 * mad, 1e-9)
    score = (residuals - median) / scale
    return {"flags": np.abs(score) > threshold, "robust_z": score, "status": "candidate_anomaly_only"}
