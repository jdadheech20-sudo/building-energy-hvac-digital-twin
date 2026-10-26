from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from building_hvac_twin.model import TwinParameters, simulate
from building_hvac_twin.synthetic import generate


@dataclass(frozen=True)
class ScenarioLimits:
    tariff_per_kwh: float
    carbon_factor_kg_per_kwh: float
    cost_budget: float
    comfort_limit_degree_hours: float
    max_peak_power_kw: float
    heating_setpoint_bounds_c: tuple[float, float]
    cooling_setpoint_bounds_c: tuple[float, float]
    equipment_max_heating_kw: float
    equipment_max_cooling_kw: float
    source_note: str

    def validate(self) -> None:
        if min(self.tariff_per_kwh, self.carbon_factor_kg_per_kwh, self.comfort_limit_degree_hours) < 0:
            raise ValueError("tariff, carbon, and comfort values must be nonnegative")
        if self.cost_budget <= 0 or self.max_peak_power_kw <= 0:
            raise ValueError("cost budget and peak equipment limit must be positive")
        h0, h1 = self.heating_setpoint_bounds_c
        c0, c1 = self.cooling_setpoint_bounds_c
        if h0 >= h1 or c0 >= c1 or h1 >= c0:
            raise ValueError("setpoint ranges must be ordered and leave a nonzero deadband")
        if min(self.equipment_max_heating_kw, self.equipment_max_cooling_kw) <= 0:
            raise ValueError("equipment limits must be positive")
        if not self.source_note.strip():
            raise ValueError("configuration provenance is required")


def _candidate_metrics(data: pd.DataFrame, params: TwinParameters, limits: ScenarioLimits, hs: float, cs: float):
    d = data.copy()
    d["heating_setpoint_c"] = hs
    d["cooling_setpoint_c"] = cs
    d["electricity_price_per_kwh"] = limits.tariff_per_kwh
    p = TwinParameters(**params.to_dict())
    p.max_heating_kw = min(p.max_heating_kw, limits.equipment_max_heating_kw)
    p.max_cooling_kw = min(p.max_cooling_kw, limits.equipment_max_cooling_kw)
    sim = simulate(d, p)
    return {
        "heating_setpoint_c": float(hs),
        "cooling_setpoint_c": float(cs),
        "energy_kwh": float(sim.predicted_energy_kwh.sum()),
        "cost": float(sim.cost.sum()),
        "carbon_kg": float(sim.predicted_energy_kwh.sum() * limits.carbon_factor_kg_per_kwh),
        "comfort_degree_hours": float(sim.comfort_violation_c.sum()),
        "peak_power_kw": float(sim.equipment_power_kw.max()),
    }


def optimize_scenario(limits: ScenarioLimits, *, horizon_days: int = 1, population: int = 48, generations: int = 35) -> dict[str, Any]:
    """Run a synthetic-only NSGA-II setpoint search under explicit limits."""
    limits.validate()
    if not 1 <= horizon_days <= 14:
        raise ValueError("horizon_days must be between 1 and 14")
    if population < 8 or generations < 1:
        raise ValueError("population must be at least 8 and generations positive")
    try:
        from pymoo.algorithms.moo.nsga2 import NSGA2
        from pymoo.core.problem import ElementwiseProblem
        from pymoo.optimize import minimize
    except ImportError as exc:
        raise RuntimeError("install the optional 'planning' extra to run NSGA-II") from exc

    data, _ = generate(days=horizon_days, seed=42)
    params = TwinParameters(
        max_heating_kw=limits.equipment_max_heating_kw,
        max_cooling_kw=limits.equipment_max_cooling_kw,
    )

    class SetpointProblem(ElementwiseProblem):
        def __init__(self):
            super().__init__(
                n_var=2,
                n_obj=4,
                n_ieq_constr=3,
                xl=np.array([limits.heating_setpoint_bounds_c[0], limits.cooling_setpoint_bounds_c[0]]),
                xu=np.array([limits.heating_setpoint_bounds_c[1], limits.cooling_setpoint_bounds_c[1]]),
            )

        def _evaluate(self, x, out, *args, **kwargs):
            hs, cs = map(float, x)
            if hs >= cs:
                out["F"] = [1e12] * 4
                out["G"] = [1e12] * 3
                return
            result = _candidate_metrics(data, params, limits, hs, cs)
            out["F"] = [result["energy_kwh"], result["cost"], result["carbon_kg"], result["comfort_degree_hours"]]
            out["G"] = [
                result["cost"] - limits.cost_budget,
                result["comfort_degree_hours"] - limits.comfort_limit_degree_hours,
                result["peak_power_kw"] - limits.max_peak_power_kw,
            ]

    result = minimize(
        SetpointProblem(),
        NSGA2(pop_size=population),
        ("n_gen", generations),
        seed=42,
        verbose=False,
    )
    if result.X is None:
        return {"feasible": False, "candidates": [], "algorithm": "NSGA-II", "source_note": limits.source_note}
    xs = np.atleast_2d(result.X)
    unique = np.unique(np.round(xs, 5), axis=0)
    candidates = []
    for hs, cs in unique[:50]:
        metrics = _candidate_metrics(data, params, limits, float(hs), float(cs))
        metrics["feasible"] = bool(
            metrics["cost"] <= limits.cost_budget
            and metrics["comfort_degree_hours"] <= limits.comfort_limit_degree_hours
            and metrics["peak_power_kw"] <= limits.max_peak_power_kw
        )
        candidates.append(metrics)
    return {
        "feasible": any(item["feasible"] for item in candidates),
        "candidates": candidates,
        "algorithm": "NSGA-II",
        "source_note": limits.source_note,
        "evidence_status": "simulated",
        "dataset_id": "synthetic-office-hourly-v1",
        "limitations": [
            "Synthetic single-zone 1R1C scenario only; not measured MLRIT performance.",
            "Tariff and carbon intensity are constant explicit inputs for this run.",
            "Comfort is a temperature-band proxy and not a compliance assessment.",
        ],
    }


def check_candidate(candidate: dict[str, float], limits: ScenarioLimits) -> dict[str, Any]:
    """Recheck scenario constraints immediately before exposing a candidate."""
    limits.validate()
    tests = {
        "cost_budget": candidate["cost"] <= limits.cost_budget,
        "comfort": candidate["comfort_degree_hours"] <= limits.comfort_limit_degree_hours,
        "peak_equipment": candidate["peak_power_kw"] <= limits.max_peak_power_kw,
        "setpoint_bounds": (
            limits.heating_setpoint_bounds_c[0] <= candidate["heating_setpoint_c"] <= limits.heating_setpoint_bounds_c[1]
            and limits.cooling_setpoint_bounds_c[0] <= candidate["cooling_setpoint_c"] <= limits.cooling_setpoint_bounds_c[1]
        ),
    }
    return {"safe": all(tests.values()), "checks": tests, "execution_mode": "simulation_only"}
