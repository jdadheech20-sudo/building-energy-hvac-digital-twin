from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx


class OpenMeteoConnector:
    """Fetch weather only when coordinates and the site's timezone are supplied."""

    url = "https://api.open-meteo.com/v1/forecast"

    @staticmethod
    def validate_request(*, latitude: float, longitude: float, timezone_name: str, days: int) -> None:
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {timezone_name}") from exc
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("latitude/longitude are outside valid ranges")
        if not 1 <= days <= 7:
            raise ValueError("days must be between 1 and 7")

    @staticmethod
    def normalize_payload(payload: dict, *, timezone_name: str):
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {timezone_name}") from exc
        hourly = payload.get("hourly") or {}
        stamps = hourly.get("time") or []
        temperatures = hourly.get("temperature_2m") or []
        humidities = hourly.get("relative_humidity_2m") or []
        if not stamps or not (len(stamps) == len(temperatures) == len(humidities)):
            raise ValueError("Open-Meteo response is missing aligned hourly weather values")
        values = []
        for stamp, temp, humidity in zip(stamps, temperatures, humidities, strict=True):
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("Open-Meteo UTC response contains a timezone-naive timestamp")
            values.append(
                {
                    "timestamp": parsed.astimezone(timezone.utc),
                    "outdoor_temp_c": temp,
                    "relative_humidity_percent": humidity,
                    "unit": {"temperature": "degC", "humidity": "%"},
                }
            )
        return {
            "source": "Open-Meteo",
            "source_url": OpenMeteoConnector.url,
            "requested_timezone": timezone_name,
            "fetched_at": datetime.now(timezone.utc),
            "evidence_status": "predicted",
            "values": values,
        }

    async def hourly(self, *, latitude: float, longitude: float, timezone_name: str, days: int = 3):
        self.validate_request(latitude=latitude, longitude=longitude, timezone_name=timezone_name, days=days)
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                self.url,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "hourly": "temperature_2m,relative_humidity_2m",
                    "timezone": "UTC",
                    "forecast_days": days,
                },
            )
            response.raise_for_status()
        return self.normalize_payload(response.json(), timezone_name=timezone_name)
