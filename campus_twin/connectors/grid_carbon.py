from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


def load_grid_carbon_csv(
    path: str | Path,
    *,
    source_name: str,
    source_url: str,
    timezone_name: str,
    timestamp_column: str = "timestamp",
    value_column: str = "value",
    unit_column: str = "unit",
):
    """Normalize a documented carbon-intensity file; never infer its units or timezone."""
    if not source_name.strip() or not source_url.strip():
        raise ValueError("source name and source URL are required")
    frame = pd.read_csv(path)
    required = {timestamp_column, value_column, unit_column}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing carbon columns: {', '.join(missing)}")
    if frame[list(required)].isna().any().any():
        raise ValueError("carbon timestamp, value, and unit cannot be missing")
    zone = ZoneInfo(timezone_name)
    timestamps = pd.to_datetime(frame[timestamp_column], errors="raise")
    if timestamps.dt.tz is None:
        timestamps = timestamps.dt.tz_localize(zone, ambiguous="raise", nonexistent="raise")
    frame["timestamp_utc"] = timestamps.dt.tz_convert("UTC")
    numeric = pd.to_numeric(frame[value_column], errors="raise")
    units = frame[unit_column].astype(str)
    converted = []
    for value, unit in zip(numeric, units, strict=True):
        if value < 0:
            raise ValueError("carbon intensity must be nonnegative")
        if unit == "kgCO2/kWh":
            converted.append(float(value))
        elif unit == "gCO2/kWh":
            converted.append(float(value) / 1000.0)
        else:
            raise ValueError(f"unsupported carbon unit {unit!r}; expected kgCO2/kWh or gCO2/kWh")
    frame["carbon_kg_co2_per_kwh"] = converted
    if frame.timestamp_utc.duplicated().any():
        raise ValueError("carbon timestamps must be unique after timezone normalization")
    return {
        "source_name": source_name,
        "source_url": source_url,
        "timezone": timezone_name,
        "fetched_at": datetime.now(timezone.utc),
        "values": frame[["timestamp_utc", "carbon_kg_co2_per_kwh"]].sort_values("timestamp_utc"),
    }
