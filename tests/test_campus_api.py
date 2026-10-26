from fastapi import HTTPException
import pytest
from campus_twin.main import create_app
from campus_twin.models import Base
from campus_twin.schemas import ChatRequest, EvidenceStatus, ForecastOut, ReadingIn
from campus_twin.seed import seed_if_empty


def initialized_app(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'campus.sqlite'}")
    Base.metadata.create_all(app.state.db_engine)
    with app.state.session_factory() as session:
        seed_if_empty(session)
    return app


def endpoints(app):
    return {
        (method, route.path): route.endpoint
        for route in app.routes if hasattr(route, "endpoint")
        for method in getattr(route, "methods", set())
    }


def test_campus_api_demo_and_read_only_tools(tmp_path):
    app = initialized_app(tmp_path)
    route = endpoints(app)
    with app.state.session_factory() as session:
        buildings = route[("GET", "/api/v1/buildings")](session=session)
        assert len(buildings) == 8
        building_id = buildings[0].id
        readings = route[("GET", "/api/v1/readings")](building_id=building_id, meter_type="electricity", limit=500, session=session)
        assert readings and readings[0].evidence_status == "synthetic"
        assert readings[0].unit == "kWh"
        forecast = ForecastOut.model_validate(
            route[("GET", "/api/v1/buildings/{building_id}/forecasts")](building_id, "electricity", 24, session)
        )
        assert forecast.source_evidence_status == "synthetic"
        assert len(forecast.points) == 24
        assert all(point.evidence_status == "predicted" for point in forecast.points)
        summary = route[("GET", "/api/v1/buildings/{building_id}/summary")](building_id, session)
        assert summary.evidence_status == "synthetic"
        assert summary.building.floor_count is None
        chat = route[("POST", "/api/v1/chat")](ChatRequest(message=f"Show energy usage for {building_id}"), session)
        assert chat.evidence_status == "synthetic"
        assert "get_building_metrics" in chat.tool_calls
        what_if = route[("POST", "/api/v1/chat")](ChatRequest(message="What if Block A cuts air-conditioning by 10 percent?"), session)
        assert "Scenario not run" in what_if.answer
        assert "run_synthetic_scenario" not in what_if.tool_calls


def test_reading_validation_rejects_naive_timestamps_and_missing_synthetic_provenance():
    base = {
        "building_id": "sr-block", "meter_type": "electricity",
        "interval_start": "2026-10-25T10:00:00", "interval_end": "2026-10-25T11:00:00",
        "value": 1.5, "unit": "kWh", "source_id": "synthetic-demo-v1",
        "evidence_status": "synthetic", "provenance": {"dataset_id": "test"},
    }
    with pytest.raises(Exception, match="timezone offset"):
        ReadingIn.model_validate(base)
    base["interval_start"] = "2026-10-25T10:00:00+00:00"
    base["interval_end"] = "2026-10-25T11:00:00+00:00"
    base["provenance"] = {}
    with pytest.raises(Exception, match="provenance"):
        ReadingIn.model_validate(base)


def test_reading_validation_requires_supported_meter_units():
    payload = {
        "building_id": "sr-block", "meter_type": "electricity",
        "interval_start": "2026-10-25T10:00:00+00:00", "interval_end": "2026-10-25T11:00:00+00:00",
        "value": 1.5, "unit": "kWh", "source_id": "synthetic-demo-v1",
        "evidence_status": "synthetic", "provenance": {"dataset_id": "test"},
    }
    assert ReadingIn.model_validate(payload).unit == "kWh"
    payload["unit"] = "furlongs"
    with pytest.raises(Exception, match="unsupported unit"):
        ReadingIn.model_validate(payload)


def test_reading_ingest_requires_registered_source_and_building(tmp_path):
    app = initialized_app(tmp_path)
    ingest = endpoints(app)[("POST", "/api/v1/readings")]
    payload = ReadingIn(
        building_id="sr-block", meter_type="electricity",
        interval_start="2000-10-25T10:00:00+00:00", interval_end="2000-10-25T11:00:00+00:00",
        value=1.5, unit="kWh", source_id="not-registered", evidence_status="measured",
        provenance={"source_record": "meter-x"},
    )
    with app.state.session_factory() as session:
        with pytest.raises(HTTPException) as not_found:
            ingest(payload, session)
        assert not_found.value.status_code == 404
        payload.source_id = "synthetic-demo-v1"
        with pytest.raises(HTTPException) as mismatched_source:
            ingest(payload, session)
        assert mismatched_source.value.status_code == 422
        payload.evidence_status = EvidenceStatus.synthetic
        created = ingest(payload, session)
        assert created.value == 1.5
        with pytest.raises(HTTPException) as duplicate:
            ingest(payload, session)
        assert duplicate.value.status_code == 409
