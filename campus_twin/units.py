"""Explicit units accepted by the campus API; values are never implicitly converted."""

SUPPORTED_METER_UNITS = {
    "electricity": {"Wh", "kWh", "MWh"},
    "chilled_water": {"kWh_thermal", "MWh_thermal", "kBtu", "ton_hour"},
    "steam": {"kWh_thermal", "MWh_thermal", "kBtu", "MMBtu", "kg"},
    "hot_water": {"kWh_thermal", "MWh_thermal", "kBtu", "MMBtu", "m3"},
    "solar": {"Wh", "kWh", "MWh"},
    "water": {"L", "m3"},
}


def unit_is_supported(meter_type: str, unit: str) -> bool:
    return unit in SUPPORTED_METER_UNITS.get(meter_type, set())
