from __future__ import annotations

from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .units import unit_is_supported


class EvidenceStatus(str, Enum):
    reference = "reference"
    measured = "measured"
    synthetic = "synthetic"
    predicted = "predicted"
    simulated = "simulated"
    mixed = "mixed"


class MeterType(str, Enum):
    electricity = "electricity"
    chilled_water = "chilled_water"
    steam = "steam"
    hot_water = "hot_water"
    solar = "solar"
    water = "water"


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    kind: str
    url: str | None = None
    license: str | None = None
    evidence_status: EvidenceStatus
    timezone: str | None = None
    nominal_interval_minutes: int | None = None
    last_fetched_at: datetime | None = None


class BuildingOut(BaseModel):
    id: str
    name: str
    category: str
    reported_area_m2: float | None = None
    floor_count: int | None = None
    layout_status: str
    position_x: float
    position_z: float
    metadata_source: str
    metadata_note: str | None = None


class ReadingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    building_id: str
    meter_type: MeterType
    interval_start: datetime
    interval_end: datetime
    value: float
    unit: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    evidence_status: EvidenceStatus
    quality_flags: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("interval_start", "interval_end")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone offset")
        return value

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("reading value must be finite")
        return value

    @field_validator("unit")
    @classmethod
    def normalize_unit_token(cls, value: str) -> str:
        unit = value.strip()
        if not unit:
            raise ValueError("unit is required")
        return unit

    @model_validator(mode="after")
    def ordered_interval(self) -> "ReadingIn":
        if self.interval_end <= self.interval_start:
            raise ValueError("interval_end must be later than interval_start")
        if not unit_is_supported(self.meter_type.value, self.unit):
            raise ValueError(f"unsupported unit {self.unit!r} for meter type {self.meter_type.value!r}")
        if self.evidence_status == EvidenceStatus.synthetic and not self.provenance:
            raise ValueError("synthetic readings must declare provenance")
        return self


class ReadingOut(ReadingIn):
    id: int


class BuildingSummary(BaseModel):
    building: BuildingOut
    latest_reading_at: datetime | None
    evidence_status: EvidenceStatus
    meters: dict[str, dict[str, float | str | None]]


class ForecastPoint(BaseModel):
    timestamp: datetime
    value: float
    lower: float | None = None
    upper: float | None = None
    meter_type: MeterType
    unit: str
    evidence_status: EvidenceStatus = EvidenceStatus.predicted


class ForecastOut(BaseModel):
    building_id: str
    model_name: str
    source_evidence_status: EvidenceStatus
    method: str
    interval_hours: int
    metrics: dict[str, float] = Field(default_factory=dict)
    points: list[ForecastPoint]
    limitations: list[str] = Field(default_factory=list)


class ScenarioRequest(BaseModel):
    horizon_days: int = Field(ge=1, le=14)
    tariff_per_kwh: float = Field(ge=0)
    carbon_factor_kg_per_kwh: float = Field(ge=0)
    cost_budget: float = Field(gt=0)
    comfort_limit_degree_hours: float = Field(ge=0)
    max_peak_power_kw: float = Field(gt=0)
    heating_setpoint_bounds_c: tuple[float, float]
    cooling_setpoint_bounds_c: tuple[float, float]
    equipment_max_heating_kw: float = Field(gt=0)
    equipment_max_cooling_kw: float = Field(gt=0)
    source_note: str = Field(min_length=4)

    @model_validator(mode="after")
    def ordered_limits(self) -> "ScenarioRequest":
        h0, h1 = self.heating_setpoint_bounds_c
        c0, c1 = self.cooling_setpoint_bounds_c
        if h0 >= h1 or c0 >= c1 or h1 >= c0:
            raise ValueError("setpoint bounds must be ordered and leave a nonzero deadband")
        return self


class ScenarioCandidate(BaseModel):
    heating_setpoint_c: float
    cooling_setpoint_c: float
    energy_kwh: float
    cost: float
    carbon_kg: float
    comfort_degree_hours: float
    peak_power_kw: float
    feasible: bool


class ScenarioOut(BaseModel):
    evidence_status: EvidenceStatus = EvidenceStatus.simulated
    algorithm: str
    source_note: str
    feasible: bool
    candidates: list[ScenarioCandidate]
    limitations: list[str]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    building_id: str | None = None
    scenario: ScenarioRequest | None = None


class ChatResponse(BaseModel):
    answer: str
    evidence_status: EvidenceStatus
    tool_calls: list[str]
    assumptions: list[str]
    source_freshness: datetime | None = None
