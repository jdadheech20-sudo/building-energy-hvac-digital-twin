import pandas as pd
import pytest

from campus_twin.connectors.grid_carbon import load_grid_carbon_csv
from campus_twin.connectors.open_meteo import OpenMeteoConnector


def test_open_meteo_normalizes_utc_and_labels_forecast():
    payload = {
        "hourly": {
            "time": ["2026-10-25T10:00Z", "2026-10-25T11:00Z"],
            "temperature_2m": [22.5, 23.0],
            "relative_humidity_2m": [54, 51],
        }
    }
    result = OpenMeteoConnector.normalize_payload(payload, timezone_name="Asia/Kolkata")
    assert result["evidence_status"] == "predicted"
    assert result["requested_timezone"] == "Asia/Kolkata"
    assert result["values"][0]["timestamp"].isoformat() == "2026-10-25T10:00:00+00:00"
    with pytest.raises(ValueError, match="valid ranges"):
        OpenMeteoConnector.validate_request(latitude=95, longitude=78, timezone_name="Asia/Kolkata", days=3)
    with pytest.raises(ValueError, match="Unknown timezone"):
        OpenMeteoConnector.validate_request(latitude=17, longitude=78, timezone_name="Mars/Olympus", days=3)
    with pytest.raises(ValueError, match="aligned hourly"):
        OpenMeteoConnector.normalize_payload({"hourly": {"time": ["2026-10-25T10:00"]}}, timezone_name="UTC")


def test_grid_carbon_connector_requires_explicit_unit_timezone_and_unique_timestamps(tmp_path):
    source = tmp_path / "carbon.csv"
    pd.DataFrame({"timestamp": ["2026-10-25 10:00"], "value": [400], "unit": ["gCO2/kWh"]}).to_csv(source, index=False)
    result = load_grid_carbon_csv(source, source_name="test grid", source_url="https://example.invalid/data", timezone_name="Asia/Kolkata")
    assert result["values"].carbon_kg_co2_per_kwh.tolist() == [0.4]
    assert result["values"].timestamp_utc.iloc[0].tzinfo is not None
    pd.DataFrame({"timestamp": ["2026-10-25 10:00"], "value": [400], "unit": ["kg per something"]}).to_csv(source, index=False)
    with pytest.raises(ValueError, match="unsupported carbon unit"):
        load_grid_carbon_csv(source, source_name="test grid", source_url="https://example.invalid/data", timezone_name="UTC")
    pd.DataFrame({"timestamp": ["2026-10-25 01:00", "2026-10-25 01:00"], "value": [400, 401], "unit": ["gCO2/kWh", "gCO2/kWh"]}).to_csv(source, index=False)
    with pytest.raises(ValueError, match="unique"):
        load_grid_carbon_csv(source, source_name="test grid", source_url="https://example.invalid/data", timezone_name="UTC")
