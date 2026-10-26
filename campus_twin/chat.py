from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import BuildingRecord, ReadingRecord
from .planning import ScenarioLimits, optimize_scenario
from .schemas import ChatResponse, EvidenceStatus, ScenarioRequest


ToolFunction = Callable[[dict[str, Any]], dict[str, Any]]


class ChatProvider(Protocol):
    def respond(self, message: str, tools: list[dict[str, Any]], execute: Callable[[str, dict[str, Any]], dict[str, Any]]) -> tuple[str, list[str]]: ...


def _json_safe(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


class ToolRegistry:
    """Small explicit allowlist. No control or equipment-write tool exists."""

    def __init__(self, session: Session, *, scenario: ScenarioRequest | None = None):
        self.session = session
        self.scenario = scenario
        self.functions: dict[str, ToolFunction] = {
            "get_building_metrics": self.get_building_metrics,
            "run_synthetic_scenario": self.run_synthetic_scenario,
            "get_data_freshness": self.get_data_freshness,
        }

    @property
    def definitions(self):
        return [
            {"toolSpec": {"name": "get_building_metrics", "description": "Read recent stored readings for a building.", "inputSchema": {"json": {"type": "object", "properties": {"building_id": {"type": "string"}}, "required": ["building_id"], "additionalProperties": False}}}},
            {"toolSpec": {"name": "run_synthetic_scenario", "description": "Run a synthetic setpoint scenario only when every assumption and constraint has been provided by the user.", "inputSchema": {"json": {"type": "object", "properties": {"confirm_use_supplied_scenario_config": {"type": "boolean"}}, "required": ["confirm_use_supplied_scenario_config"], "additionalProperties": False}}}},
            {"toolSpec": {"name": "get_data_freshness", "description": "Read the latest source and meter timestamps.", "inputSchema": {"json": {"type": "object", "properties": {}, "additionalProperties": False}}}},
        ]

    def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        function = self.functions.get(name)
        if function is None:
            raise ValueError("tool is not in the permitted campus assistant allowlist")
        result = function(args)
        return _json_safe(result)

    def get_building_metrics(self, args: dict[str, Any]) -> dict[str, Any]:
        building_id = str(args.get("building_id", ""))
        building = self.session.get(BuildingRecord, building_id)
        if building is None:
            raise ValueError(f"unknown building_id: {building_id}")
        latest = self.session.scalars(
            select(ReadingRecord)
            .where(ReadingRecord.building_id == building_id)
            .order_by(ReadingRecord.interval_end.desc())
            .limit(200)
        ).all()
        meters: dict[str, dict[str, Any]] = {}
        for reading in latest:
            meters.setdefault(reading.meter_type, {"value": reading.value, "unit": reading.unit, "timestamp": reading.interval_end, "evidence_status": reading.evidence_status})
        return {"building_id": building.id, "building_name": building.name, "meters": meters}

    def get_data_freshness(self, _args: dict[str, Any]) -> dict[str, Any]:
        latest = self.session.scalar(select(ReadingRecord.interval_end).order_by(ReadingRecord.interval_end.desc()).limit(1))
        statuses = set(self.session.scalars(select(ReadingRecord.evidence_status).distinct()).all())
        evidence = next(iter(statuses)) if len(statuses) == 1 else ("mixed" if statuses else "reference")
        return {"latest_reading_at": latest, "server_time": datetime.now(timezone.utc), "evidence_status": evidence}

    def run_synthetic_scenario(self, args: dict[str, Any]) -> dict[str, Any]:
        if args.get("confirm_use_supplied_scenario_config") is not True:
            raise ValueError("scenario execution requires an explicit confirmation flag")
        if self.scenario is None:
            raise ValueError("scenario tariff, carbon, comfort, budget, setpoints, and equipment limits must be supplied")
        limits = ScenarioLimits(
            tariff_per_kwh=self.scenario.tariff_per_kwh,
            carbon_factor_kg_per_kwh=self.scenario.carbon_factor_kg_per_kwh,
            cost_budget=self.scenario.cost_budget,
            comfort_limit_degree_hours=self.scenario.comfort_limit_degree_hours,
            max_peak_power_kw=self.scenario.max_peak_power_kw,
            heating_setpoint_bounds_c=self.scenario.heating_setpoint_bounds_c,
            cooling_setpoint_bounds_c=self.scenario.cooling_setpoint_bounds_c,
            equipment_max_heating_kw=self.scenario.equipment_max_heating_kw,
            equipment_max_cooling_kw=self.scenario.equipment_max_cooling_kw,
            source_note=self.scenario.source_note,
        )
        return optimize_scenario(limits, horizon_days=self.scenario.horizon_days)


class DemoProvider:
    """Deterministic no-credential chat path for local demos and tests."""

    def respond(self, message: str, tools: list[dict[str, Any]], execute):
        text = message.lower()
        building_ids = [word.strip(" .,?!:;") for word in message.split() if "-block" in word.lower()]
        building_id = building_ids[0].lower().replace("_", "-") if building_ids else ""
        calls = []
        if any(term in text for term in ("metric", "energy", "usage", "consumption", "forecast")):
            if building_id:
                data = execute("get_building_metrics", {"building_id": building_id})
                calls.append("get_building_metrics")
                if data["meters"]:
                    details = ", ".join(f"{k}: {v['value']} {v['unit']}" for k, v in data["meters"].items())
                    answer = f"Recent stored values for {data['building_name']}: {details}. These values are labelled {next(iter(data['meters'].values()))['evidence_status']}."
                else:
                    answer = f"No meter readings are stored for {data['building_name']}."
            else:
                data = execute("get_data_freshness", {})
                calls.append("get_data_freshness")
                answer = f"The latest stored meter interval ends at {data['latest_reading_at']}. Current demo data are synthetic; no measured MLRIT meter records are connected."
        elif any(term in text for term in ("what if", "scenario", "intervention", "optimize")):
            try:
                data = execute("run_synthetic_scenario", {"confirm_use_supplied_scenario_config": True})
                candidate = next((item for item in data["candidates"] if item["feasible"]), None)
                if candidate is None:
                    answer = "The simulated search found no candidate that satisfies the supplied constraints. No campus operating action is available through chat."
                else:
                    answer = (
                        "Simulated scenario estimate using the supplied assumptions: "
                        f"{candidate['energy_kwh']:.1f} kWh, cost {candidate['cost']:.2f}, "
                        f"carbon {candidate['carbon_kg']:.1f} kg, comfort proxy "
                        f"{candidate['comfort_degree_hours']:.1f} degree-hours. "
                        "This uses the synthetic 1R1C model and cannot control equipment."
                    )
                calls.append("run_synthetic_scenario")
            except (ValueError, RuntimeError) as exc:
                answer = f"Scenario not run: {exc}. No campus operating action is available through chat."
        else:
            answer = "I can retrieve building metrics, report data freshness, or run a configured synthetic scenario. Ask about a building using its block ID, such as sr-block."
        return answer, calls


class BedrockConverseProvider:
    def __init__(self, *, model_id: str, region_name: str | None = None, max_tool_rounds: int = 3):
        if not model_id:
            raise ValueError("BEDROCK_MODEL_ID is required for the Bedrock adapter")
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("install the optional 'chat-bedrock' extra to use Amazon Bedrock") from exc
        self.client = boto3.client("bedrock-runtime", region_name=region_name)
        self.model_id = model_id
        self.max_tool_rounds = max_tool_rounds

    def respond(self, message: str, tools: list[dict[str, Any]], execute):
        messages: list[dict[str, Any]] = [{"role": "user", "content": [{"text": message}]}]
        tool_calls: list[str] = []
        for _ in range(self.max_tool_rounds + 1):
            response = self.client.converse(
                modelId=self.model_id,
                messages=messages,
                toolConfig={"tools": tools},
                system=[{"text": "You are a campus resource analysis assistant. Use only supplied data and allowlisted tools. Never operate equipment. Clearly state when results are synthetic, predicted, or simulated. Do not invent missing values."}],
            )
            assistant_message = response["output"]["message"]
            messages.append(assistant_message)
            requested = [block["toolUse"] for block in assistant_message.get("content", []) if "toolUse" in block]
            if not requested:
                answer = "\n".join(block.get("text", "") for block in assistant_message.get("content", []) if "text" in block).strip()
                return answer, tool_calls
            results = []
            for tool in requested:
                name = str(tool.get("name", ""))
                try:
                    result = execute(name, tool.get("input") or {})
                    results.append({"toolResult": {"toolUseId": tool["toolUseId"], "content": [{"json": result}]}})
                    tool_calls.append(name)
                except Exception as exc:
                    results.append({"toolResult": {"toolUseId": tool["toolUseId"], "status": "error", "content": [{"text": str(exc)}]}})
            messages.append({"role": "user", "content": results})
        return "I could not finish the requested analysis within the tool-call limit. No physical controls were changed.", tool_calls


def answer_chat(session: Session, message: str, *, building_id: str | None = None, scenario: ScenarioRequest | None = None, provider_name: str = "demo", model_id: str | None = None, region_name: str | None = None) -> ChatResponse:
    registry = ToolRegistry(session, scenario=scenario)
    if provider_name == "bedrock":
        provider: ChatProvider = BedrockConverseProvider(model_id=model_id or "", region_name=region_name)
    else:
        provider = DemoProvider()
    answer, calls = provider.respond(message, registry.definitions, registry.execute)
    latest = session.scalar(select(ReadingRecord.interval_end).order_by(ReadingRecord.interval_end.desc()).limit(1))
    statuses = set(session.scalars(select(ReadingRecord.evidence_status).distinct()).all())
    if "simulated" in answer.lower():
        status = EvidenceStatus.simulated
    elif len(statuses) == 1:
        status = EvidenceStatus(next(iter(statuses)))
    elif statuses:
        status = EvidenceStatus.mixed
    else:
        status = EvidenceStatus.reference
    assumptions = ["The seeded campus time series is synthetic.", "The assistant has read-only data tools and no equipment-control tool."]
    if scenario is not None:
        assumptions.append(f"Scenario constraints were supplied with note: {scenario.source_note}")
    if building_id and not calls:
        assumptions.append(f"Requested building context: {building_id}")
    return ChatResponse(answer=answer, evidence_status=status, tool_calls=calls, assumptions=assumptions, source_freshness=latest)
