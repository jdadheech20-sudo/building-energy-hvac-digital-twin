from campus_twin.planning import ScenarioLimits, check_candidate, optimize_scenario


def limits(**updates):
    values = {
        "tariff_per_kwh": 0.18,
        "carbon_factor_kg_per_kwh": 0.42,
        "cost_budget": 1_000_000.0,
        "comfort_limit_degree_hours": 10_000.0,
        "max_peak_power_kw": 1_000.0,
        "heating_setpoint_bounds_c": (18.0, 21.0),
        "cooling_setpoint_bounds_c": (24.0, 28.0),
        "equipment_max_heating_kw": 30.0,
        "equipment_max_cooling_kw": 30.0,
        "source_note": "synthetic test assumptions",
    }
    values.update(updates)
    return ScenarioLimits(**values)


def test_nsga2_returns_simulated_candidates_and_rechecks_constraints():
    result = optimize_scenario(limits(), horizon_days=1, population=8, generations=2)
    assert result["algorithm"] == "NSGA-II"
    assert result["evidence_status"] == "simulated"
    assert result["candidates"]
    for candidate in result["candidates"]:
        check = check_candidate(candidate, limits())
        assert check["execution_mode"] == "simulation_only"
        assert check["safe"] == candidate["feasible"]


def test_candidate_safety_layer_rejects_budget_comfort_peak_and_setpoint_violations():
    configured = limits(cost_budget=2.0, comfort_limit_degree_hours=0.2, max_peak_power_kw=0.1)
    candidate = {
        "heating_setpoint_c": 17.0,
        "cooling_setpoint_c": 30.0,
        "cost": 3.0,
        "comfort_degree_hours": 1.0,
        "peak_power_kw": 2.0,
    }
    result = check_candidate(candidate, configured)
    assert not result["safe"]
    assert not all(result["checks"].values())
