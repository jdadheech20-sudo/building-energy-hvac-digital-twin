from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import BuildingRecord, ReadingRecord, SourceRecord

ROOT = Path(__file__).resolve().parent
BUILDINGS_FILE = ROOT / "data" / "mlrit_buildings.json"
REPORT_URL = "https://naac.mlrit.ac.in/assets/2ndcyclessr.pdf"
TOUR_URL = "https://mlrit.ac.in/student-life/discover-mlr"


def seed_if_empty(session: Session) -> None:
    if session.scalar(select(func.count()).select_from(BuildingRecord)):
        return
    definitions = json.loads(BUILDINGS_FILE.read_text(encoding="utf-8"))
    session.add_all(
        [
            SourceRecord(
                id="mlrit-2023-report",
                name="MLRIT NAAC Self Study Report, 2023",
                kind="institutional_reference",
                url=REPORT_URL,
                evidence_status="reference",
            ),
            SourceRecord(
                id="mlrit-campus-tour",
                name="MLRIT official virtual campus tour",
                kind="visual_reference",
                url=TOUR_URL,
                evidence_status="reference",
            ),
            SourceRecord(
                id="synthetic-demo-v1",
                name="Deterministic synthetic campus meter demo",
                kind="generated_example",
                evidence_status="synthetic",
                timezone="UTC",
                nominal_interval_minutes=60,
            ),
        ]
    )
    session.flush()
    buildings = [
        BuildingRecord(
            id=item["id"],
            name=item["name"],
            category=item["category"],
            reported_area_m2=item["reported_area_m2"],
            floor_count=item["floor_count"],
            position_x=item["position_x"],
            position_z=item["position_z"],
            layout_status="schematic",
            metadata_source=item["metadata_source"],
            metadata_note=item["metadata_note"],
        )
        for item in definitions
    ]
    session.add_all(buildings)
    session.flush()

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    meters = (
        ("electricity", "kWh"),
        ("chilled_water", "kWh_thermal"),
        ("steam", "kWh_thermal"),
        ("hot_water", "kWh_thermal"),
    )
    readings = []
    for b_index, building in enumerate(buildings):
        for meter_index, (meter_type, unit) in enumerate(meters):
            for age in range(48, 0, -1):
                end = now - timedelta(hours=age)
                hour = end.hour
                daily = 1.0 + 0.35 * (hour in range(8, 19)) + 0.12 * (hour in range(16, 21))
                value = (7.0 + b_index * 0.7 + meter_index * 1.4) * daily
                readings.append(
                    ReadingRecord(
                        building_id=building.id,
                        meter_type=meter_type,
                        interval_start=end - timedelta(hours=1),
                        interval_end=end,
                        value=round(value, 3),
                        unit=unit,
                        source_id="synthetic-demo-v1",
                        evidence_status="synthetic",
                        quality_flags=[],
                        provenance={
                            "dataset_id": "mlrit-synthetic-campus-demo-v1",
                            "generator": "campus_twin.seed",
                            "seed": 42,
                            "interval_semantics": "interval energy ending at interval_end",
                            "hour_locality": "UTC",
                        },
                    )
                )
    session.add_all(readings)
    session.commit()
