from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from campus_twin.forecasting import (
    feature_columns_for_history,
    load_ashrae_training_data,
    make_supervised,
    metric_report,
    persistence_predictions,
    robust_residual_flags,
    split_chronologically,
    split_unseen_buildings,
    TimeSplit,
    validate_model_frame,
)


def frame(hours=220, buildings=(1, 2)):
    timestamps = pd.date_range("2016-01-01", periods=hours, freq="h", tz="UTC")
    return pd.DataFrame(
        [
            {"building_id": bid, "meter_type": "electricity", "timestamp": stamp, "value": 10 + bid + i % 24, "unit": "kWh"}
            for bid in buildings
            for i, stamp in enumerate(timestamps)
        ]
    )


def test_quality_lags_splits_metrics_and_limited_history_features():
    source = frame()
    assert validate_model_frame(source) == []
    supervised = make_supervised(source)
    target = supervised[(supervised.building_id == 1) & (supervised.timestamp == pd.Timestamp("2016-01-08 00:00:00+00:00"))].iloc[0]
    assert target.lag_24 == 11
    assert target.lag_168 == 11
    train, calibration, test = split_chronologically(
        supervised.loc[supervised.building_id == 1],
        split=TimeSplit(
            train_end=datetime(2016, 1, 6, tzinfo=timezone.utc),
            calibration_end=datetime(2016, 1, 7, tzinfo=timezone.utc),
            test_start=datetime(2016, 1, 8, tzinfo=timezone.utc),
        ),
    )
    assert train.timestamp.max() < calibration.timestamp.max() < test.timestamp.min()
    known, unseen = split_unseen_buildings(supervised, heldout_building_ids={2})
    assert set(known.building_id) == {1}
    assert set(unseen.building_id) == {2}
    assert feature_columns_for_history(0) == ["hour_sin", "hour_cos", "day_sin", "day_cos", "month", "square_feet", "year_built", "air_temperature", "dew_temperature", "relative_humidity", "wind_speed"]
    assert "lag_24" in feature_columns_for_history(1)
    assert "lag_168" not in feature_columns_for_history(1)
    assert "lag_168" in feature_columns_for_history(7)
    assert metric_report([1, 2], [2, 2])["mae"] == 0.5
    assert persistence_predictions(supervised.dropna(subset=["lag_24", "lag_168"]).head(5))["weekly_persistence"].shape == (5,)


def test_frame_rejects_naive_timestamps_missing_units_and_duplicate_rows():
    source = frame(hours=30, buildings=(1,))
    source["timestamp"] = source["timestamp"].astype(object)
    source.loc[0, "timestamp"] = pd.Timestamp("2016-01-01")
    assert any("timezone-aware" in error for error in validate_model_frame(source))
    source = frame(hours=30, buildings=(1,))
    source.loc[0, "unit"] = ""
    assert any("unit is required" in error for error in validate_model_frame(source))
    source = frame(hours=30, buildings=(1,))
    source.loc[0, "unit"] = "furlongs"
    assert any("unsupported meter type/unit pairs" in error for error in validate_model_frame(source))
    source = pd.concat([source, source.iloc[[0]]], ignore_index=True)
    assert any("must be unique" in error for error in validate_model_frame(source))


def test_timestamp_lag_uses_real_24_and_168_hour_offsets_when_rows_are_missing():
    source = frame(hours=250, buildings=(1,))
    source = source.drop(index=[2]).reset_index(drop=True)
    supervised = make_supervised(source)
    row = supervised.loc[supervised.timestamp == pd.Timestamp("2016-01-02 02:00:00+00:00")].iloc[0]
    assert np.isnan(row.lag_24)
    row = supervised.loc[supervised.timestamp == pd.Timestamp("2016-01-08 02:00:00+00:00")].iloc[0]
    assert row.lag_24 == 10 + 1 + (146 % 24)
    assert np.isnan(row.lag_168)


def test_residual_flags_require_history_and_identify_large_outlier():
    short = robust_residual_flags([1, 2], [1, 2])
    assert short["status"] == "insufficient_history"
    result = robust_residual_flags([1] * 29 + [100], [1] * 30)
    assert result["flags"][-1]


def test_ashrae_loader_requires_explicit_site_timezone_and_meter_units(tmp_path):
    pd.DataFrame({"building_id": [1], "meter": [0], "timestamp": ["2016-01-01 00:00:00"], "meter_reading": [3.0]}).to_csv(tmp_path / "train.csv", index=False)
    pd.DataFrame({"building_id": [1], "site_id": [0], "primary_use": ["Education"], "square_feet": [1000], "year_built": [2000]}).to_csv(tmp_path / "building_metadata.csv", index=False)
    pd.DataFrame({"site_id": [0], "timestamp": ["2016-01-01 00:00:00"], "air_temperature": [10.0], "dew_temperature": [5.0], "relative_humidity": [50.0], "wind_speed": [2.0]}).to_csv(tmp_path / "weather_train.csv", index=False)
    with pytest.raises(ValueError, match="explicit meter units"):
        load_ashrae_training_data(tmp_path, site_timezones={0: "UTC"}, meter_units={})
    with pytest.raises(ValueError, match="explicit timezone missing"):
        load_ashrae_training_data(tmp_path, site_timezones={}, meter_units={"electricity": "kWh", "chilled_water": "kWh", "steam": "kBtu", "hot_water": "kBtu"})
    loaded = load_ashrae_training_data(tmp_path, site_timezones={0: "UTC"}, meter_units={"electricity": "kWh", "chilled_water": "kWh", "steam": "kBtu", "hot_water": "kBtu"})
    assert loaded.timestamp.iloc[0].tzinfo is not None
    assert loaded.unit.iloc[0] == "kWh"


def test_ashrae_loader_rejects_ambiguous_local_dst_time(tmp_path):
    pd.DataFrame({"building_id": [1], "meter": [0], "timestamp": ["2021-11-07 01:00:00"], "meter_reading": [3.0]}).to_csv(tmp_path / "train.csv", index=False)
    pd.DataFrame({"building_id": [1], "site_id": [0], "primary_use": ["Education"], "square_feet": [1000], "year_built": [2000]}).to_csv(tmp_path / "building_metadata.csv", index=False)
    pd.DataFrame({"site_id": [0], "timestamp": ["2021-11-07 01:00:00"], "air_temperature": [10.0], "dew_temperature": [5.0], "relative_humidity": [50.0], "wind_speed": [2.0]}).to_csv(tmp_path / "weather_train.csv", index=False)
    units = {"electricity": "kWh", "chilled_water": "kWh", "steam": "kBtu", "hot_water": "kBtu"}
    with pytest.raises(Exception):
        load_ashrae_training_data(tmp_path, site_timezones={0: "America/New_York"}, meter_units=units)
