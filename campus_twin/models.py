from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class BuildingRecord(Base):
    __tablename__ = "buildings"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    reported_area_m2: Mapped[float | None] = mapped_column(Float)
    floor_count: Mapped[int | None] = mapped_column(Integer)
    layout_status: Mapped[str] = mapped_column(String(40), nullable=False)
    position_x: Mapped[float] = mapped_column(Float, nullable=False)
    position_z: Mapped[float] = mapped_column(Float, nullable=False)
    metadata_source: Mapped[str] = mapped_column(String(300), nullable=False)
    metadata_note: Mapped[str | None] = mapped_column(Text)


class SourceRecord(Base):
    __tablename__ = "data_sources"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(160))
    evidence_status: Mapped[str] = mapped_column(String(24), nullable=False)
    timezone: Mapped[str | None] = mapped_column(String(80))
    nominal_interval_minutes: Mapped[int | None] = mapped_column(Integer)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReadingRecord(Base):
    __tablename__ = "meter_readings"
    __table_args__ = (
        UniqueConstraint("building_id", "meter_type", "interval_start", "source_id", name="uq_reading_source_interval"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    building_id: Mapped[str] = mapped_column(ForeignKey("buildings.id"), index=True)
    meter_type: Mapped[str] = mapped_column(String(40), index=True)
    interval_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    interval_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(ForeignKey("data_sources.id"))
    evidence_status: Mapped[str] = mapped_column(String(24), nullable=False)
    quality_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class ForecastRunRecord(Base):
    __tablename__ = "forecast_runs"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    building_id: Mapped[str | None] = mapped_column(ForeignKey("buildings.id"))
    meter_type: Mapped[str] = mapped_column(String(40), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_status: Mapped[str] = mapped_column(String(24), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ScenarioRecord(Base):
    __tablename__ = "scenarios"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    evidence_status: Mapped[str] = mapped_column(String(24), nullable=False)
    feasible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    assumptions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    outcomes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
