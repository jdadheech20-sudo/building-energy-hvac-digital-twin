from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .chat import answer_chat
from .config import settings
from .connectors.open_meteo import OpenMeteoConnector
from .db import SessionLocal, make_engine
from .forecast_service import as_utc, forecast_from_readings, latest_robust_anomaly
from .models import Base, BuildingRecord, ForecastRunRecord, ReadingRecord, ScenarioRecord, SourceRecord
from .planning import ScenarioLimits, check_candidate, optimize_scenario
from .schemas import (
    BuildingOut,
    BuildingSummary,
    ChatRequest,
    ChatResponse,
    ForecastOut,
    ReadingIn,
    ReadingOut,
    ScenarioCandidate,
    ScenarioOut,
    ScenarioRequest,
    SourceOut,
)
from .seed import seed_if_empty


def _building(row: BuildingRecord) -> BuildingOut:
    return BuildingOut(
        id=row.id,
        name=row.name,
        category=row.category,
        reported_area_m2=row.reported_area_m2,
        floor_count=row.floor_count,
        layout_status=row.layout_status,
        position_x=row.position_x,
        position_z=row.position_z,
        metadata_source=row.metadata_source,
        metadata_note=row.metadata_note,
    )


def _reading(row: ReadingRecord) -> ReadingOut:
    return ReadingOut(
        id=row.id,
        building_id=row.building_id,
        meter_type=row.meter_type,
        interval_start=as_utc(row.interval_start),
        interval_end=as_utc(row.interval_end),
        value=row.value,
        unit=row.unit,
        source_id=row.source_id,
        evidence_status=row.evidence_status,
        quality_flags=row.quality_flags or [],
        provenance=row.provenance or {},
    )


def create_app(database_url: str | None = None, *, seed_demo: bool = True) -> FastAPI:
    db_engine = make_engine(database_url)
    session_factory = SessionLocal if database_url is None else sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        Base.metadata.create_all(db_engine)
        if seed_demo:
            with session_factory() as session:
                seed_if_empty(session)
        yield
        db_engine.dispose()

    app = FastAPI(title="MLRIT Sustainable Campus Twin API", version="0.1.0", lifespan=lifespan)
    app.state.db_engine = db_engine
    app.state.session_factory = session_factory

    def get_db():
        with session_factory() as session:
            yield session

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "service": "campus-twin-api", "now": datetime.now(timezone.utc)}

    @app.get("/api/v1/buildings", response_model=list[BuildingOut])
    def buildings(session: Session = Depends(get_db)):
        return [_building(x) for x in session.scalars(select(BuildingRecord).order_by(BuildingRecord.name)).all()]

    @app.get("/api/v1/sources", response_model=list[SourceOut])
    def sources(session: Session = Depends(get_db)):
        return session.scalars(select(SourceRecord).order_by(SourceRecord.id)).all()

    @app.get("/api/v1/readings", response_model=list[ReadingOut])
    def readings(
        building_id: str | None = None,
        meter_type: str | None = None,
        limit: int = Query(default=500, ge=1, le=5000),
        session: Session = Depends(get_db),
    ):
        query = select(ReadingRecord)
        if building_id:
            query = query.where(ReadingRecord.building_id == building_id)
        if meter_type:
            query = query.where(ReadingRecord.meter_type == meter_type)
        rows = session.scalars(query.order_by(ReadingRecord.interval_end.desc()).limit(limit)).all()
        return [_reading(x) for x in rows]

    @app.post("/api/v1/readings", response_model=ReadingOut, status_code=201)
    def ingest_reading(payload: ReadingIn, session: Session = Depends(get_db)):
        if session.get(BuildingRecord, payload.building_id) is None:
            raise HTTPException(404, "building_id not found")
        source = session.get(SourceRecord, payload.source_id)
        if source is None:
            raise HTTPException(404, "source_id not found; register source provenance first")
        if payload.evidence_status.value != source.evidence_status:
            raise HTTPException(422, "reading evidence_status must match its registered source")
        if payload.value < 0:
            raise HTTPException(422, "meter readings must be nonnegative")
        duplicate = session.scalar(
            select(ReadingRecord.id).where(
                ReadingRecord.building_id == payload.building_id,
                ReadingRecord.meter_type == payload.meter_type.value,
                ReadingRecord.interval_start == payload.interval_start,
                ReadingRecord.source_id == payload.source_id,
            ).limit(1)
        )
        if duplicate is not None:
            raise HTTPException(409, "duplicate reading for building, meter, interval, and source")
        row = ReadingRecord(**payload.model_dump())
        session.add(row)
        session.commit()
        session.refresh(row)
        return _reading(row)

    @app.get("/api/v1/buildings/{building_id}/summary", response_model=BuildingSummary)
    def building_summary(building_id: str, session: Session = Depends(get_db)):
        building = session.get(BuildingRecord, building_id)
        if building is None:
            raise HTTPException(404, "building_id not found")
        recent = session.scalars(
            select(ReadingRecord).where(ReadingRecord.building_id == building_id).order_by(ReadingRecord.interval_end.desc()).limit(96)
        ).all()
        latest = max((as_utc(r.interval_end) for r in recent), default=None)
        meters: dict[str, dict] = {}
        for item in recent:
            entry = meters.setdefault(item.meter_type, {"value": 0.0, "unit": item.unit, "evidence_status": item.evidence_status, "intervals": 0})
            if item.unit != entry["unit"]:
                raise HTTPException(409, f"inconsistent units for meter type {item.meter_type}")
            entry["value"] = float(entry["value"]) + item.value
            entry["intervals"] += 1
        statuses = {r.evidence_status for r in recent}
        evidence = next(iter(statuses)) if len(statuses) == 1 else ("mixed" if statuses else "reference")
        return BuildingSummary(building=_building(building), latest_reading_at=latest, evidence_status=evidence, meters=meters)

    @app.get("/api/v1/buildings/{building_id}/forecasts", response_model=ForecastOut)
    def forecast(building_id: str, meter_type: str = "electricity", horizon: int = Query(default=24, ge=1, le=168), session: Session = Depends(get_db)):
        if session.get(BuildingRecord, building_id) is None:
            raise HTTPException(404, "building_id not found")
        rows = session.scalars(
            select(ReadingRecord).where(ReadingRecord.building_id == building_id, ReadingRecord.meter_type == meter_type).order_by(ReadingRecord.interval_end.desc()).limit(400)
        ).all()
        try:
            result = forecast_from_readings(building_id, meter_type, list(reversed(rows)), horizon)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if rows:
            run = ForecastRunRecord(
                id=str(uuid.uuid4()),
                building_id=building_id,
                meter_type=meter_type,
                model_name=result["model_name"],
                metrics=result["metrics"],
                evidence_status="predicted",
                details={"source_evidence_status": result["source_evidence_status"], "method": result["method"]},
            )
            session.add(run)
            session.commit()
        return result

    @app.get("/api/v1/buildings/{building_id}/anomalies")
    def anomalies(building_id: str, meter_type: str = "electricity", session: Session = Depends(get_db)):
        if session.get(BuildingRecord, building_id) is None:
            raise HTTPException(404, "building_id not found")
        rows = session.scalars(
            select(ReadingRecord).where(ReadingRecord.building_id == building_id, ReadingRecord.meter_type == meter_type).order_by(ReadingRecord.interval_end.desc()).limit(200)
        ).all()
        result = latest_robust_anomaly(list(reversed(rows)))
        result.update({"building_id": building_id, "meter_type": meter_type, "evidence_status": "predicted", "interpretation": "candidate anomaly flag; not a fault diagnosis"})
        return result

    @app.post("/api/v1/weather/open-meteo")
    async def weather_open_meteo(latitude: float, longitude: float, timezone_name: str, days: int = Query(default=3, ge=1, le=7)):
        try:
            return await OpenMeteoConnector().hourly(latitude=latitude, longitude=longitude, timezone_name=timezone_name, days=days)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/v1/scenarios/optimize", response_model=ScenarioOut)
    def optimize(payload: ScenarioRequest, session: Session = Depends(get_db)):
        limits = ScenarioLimits(
            tariff_per_kwh=payload.tariff_per_kwh,
            carbon_factor_kg_per_kwh=payload.carbon_factor_kg_per_kwh,
            cost_budget=payload.cost_budget,
            comfort_limit_degree_hours=payload.comfort_limit_degree_hours,
            max_peak_power_kw=payload.max_peak_power_kw,
            heating_setpoint_bounds_c=payload.heating_setpoint_bounds_c,
            cooling_setpoint_bounds_c=payload.cooling_setpoint_bounds_c,
            equipment_max_heating_kw=payload.equipment_max_heating_kw,
            equipment_max_cooling_kw=payload.equipment_max_cooling_kw,
            source_note=payload.source_note,
        )
        try:
            result = optimize_scenario(limits, horizon_days=payload.horizon_days)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        candidates = [ScenarioCandidate(**c) for c in result["candidates"]]
        checks = [check_candidate(c.model_dump(), limits) for c in candidates]
        result["feasible"] = any(check["safe"] for check in checks)
        result["limitations"] = result.get("limitations", []) + ["Safety checks are reevaluated before candidates are returned."]
        row = ScenarioRecord(
            id=str(uuid.uuid4()),
            evidence_status="simulated",
            feasible=result["feasible"],
            assumptions=payload.model_dump(mode="json"),
            outcomes=[c.model_dump() for c in candidates],
        )
        session.add(row)
        session.commit()
        return result

    @app.post("/api/v1/chat", response_model=ChatResponse)
    def chat(payload: ChatRequest, session: Session = Depends(get_db)):
        try:
            return answer_chat(
                session,
                payload.message,
                building_id=payload.building_id,
                scenario=payload.scenario,
                provider_name=settings.assistant_provider,
                model_id=settings.bedrock_model_id,
                region_name=settings.aws_region,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    return app


app = create_app()
