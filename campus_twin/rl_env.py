from __future__ import annotations

from typing import Any

import numpy as np
from gymnasium.spaces import Box

try:
    from ray.rllib.env.multi_agent_env import MultiAgentEnv
except ImportError:  # Simulation baselines remain usable without the optional RLlib extra.
    class MultiAgentEnv:
        def reset(self, *, seed: int | None = None, options: dict | None = None):
            return None

from building_hvac_twin.model import TwinParameters, simulate
from building_hvac_twin.synthetic import generate


class CampusMultiAgentEnv(MultiAgentEnv):
    """Single-node simulated hierarchy: a campus coordinator plus one agent per building."""

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__()
        config = config or {}
        self.building_ids = list(config.get("building_ids") or ["sr-block", "ak-block", "jc-block"])
        if not self.building_ids:
            raise ValueError("at least one building agent is required")
        self.peak_limit_kw = float(config["peak_limit_kw"])
        self.comfort_penalty = float(config.get("comfort_penalty", 25.0))
        if self.peak_limit_kw <= 0 or self.comfort_penalty < 0:
            raise ValueError("peak limit must be positive and comfort penalty nonnegative")
        self.coordinator_id = "campus-coordinator"
        self.possible_agents = [f"building:{bid}" for bid in self.building_ids] + [self.coordinator_id]
        self.agents = self.possible_agents.copy()
        obs_spaces = {agent: Box(low=-100, high=1000, shape=(5,), dtype=np.float32) for agent in self.possible_agents}
        action_spaces = {agent: Box(low=-1, high=1, shape=(1 if agent == self.coordinator_id else 2,), dtype=np.float32) for agent in self.possible_agents}
        self.observation_spaces = obs_spaces
        self.action_spaces = action_spaces
        self.observation_space = Box(low=-100, high=1000, shape=(5,), dtype=np.float32)
        self.action_space = Box(low=-1, high=1, shape=(2,), dtype=np.float32)
        self.frames = {bid: generate(days=1, seed=42 + i)[0] for i, bid in enumerate(self.building_ids)}
        self.params = TwinParameters()
        self.reset()

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        self.step_index = 0
        self.temperature = {bid: 20.0 for bid in self.building_ids}
        self.energy_used = 0.0
        self.agents = self.possible_agents.copy()
        self._last = {}
        return self._observations(), {agent: {"evidence_status": "synthetic"} for agent in self.agents}

    def _observations(self):
        result = {}
        for bid in self.building_ids:
            row = self.frames[bid].iloc[min(self.step_index, 23)]
            result[f"building:{bid}"] = np.asarray([
                float(row.outdoor_temp_c),
                float(row.occupancy_count),
                self.temperature[bid],
                self.energy_used,
                float(row.equipment_status),
            ], dtype=np.float32)
        result[self.coordinator_id] = np.asarray([
            self.step_index,
            float(np.mean(list(self.temperature.values()))),
            self.energy_used,
            float(len(self.building_ids)),
            0.0,
        ], dtype=np.float32)
        return result

    def step(self, action_dict):
        expected = set(self.possible_agents)
        if set(action_dict) != expected:
            raise ValueError("one action is required from the coordinator and every building agent")
        values = {agent: np.asarray(action_dict[agent], dtype=float).reshape(-1) for agent in expected}
        if any(not np.isfinite(v).all() or np.any(v < -1) or np.any(v > 1) for v in values.values()):
            raise ValueError("out-of-range or non-finite actions are rejected by the safety layer")
        shared = float(values[self.coordinator_id][0]) * 0.5
        outputs = {}
        total_power = 0.0
        total_cost = 0.0
        total_comfort = 0.0
        for bid in self.building_ids:
            agent = f"building:{bid}"
            row = self.frames[bid].iloc[[min(self.step_index, 23)]].copy()
            local_h, local_c = values[agent] * 0.5
            row["heating_setpoint_c"] = np.clip(row.heating_setpoint_c + shared + local_h, 17.0, 22.0)
            row["cooling_setpoint_c"] = np.clip(row.cooling_setpoint_c + shared + local_c, 23.0, 29.0)
            sim = simulate(row, self.params, initial_temp_c=self.temperature[bid])
            self.temperature[bid] = float(sim.predicted_indoor_temp_c.iloc[0])
            power = float(sim.equipment_power_kw.iloc[0])
            cost = float(sim.cost.iloc[0])
            comfort = float(sim.comfort_violation_c.iloc[0])
            total_power += power
            total_cost += cost
            total_comfort += comfort
            outputs[agent] = {"energy_kwh": float(sim.predicted_energy_kwh.iloc[0]), "cost": cost, "comfort_violation_c": comfort, "evidence_status": "simulated"}
        self._last = {"peak_power_kw": total_power, "cost": total_cost, "comfort_degree_hours": total_comfort}
        unsafe = total_power > self.peak_limit_kw
        self.energy_used += sum(v["energy_kwh"] for v in outputs.values())
        reward = {
            f"building:{bid}": -(outputs[f"building:{bid}"]["cost"] + self.comfort_penalty * outputs[f"building:{bid}"]["comfort_violation_c"])
            for bid in self.building_ids
        }
        reward[self.coordinator_id] = -(total_cost + self.comfort_penalty * total_comfort + (1000.0 if unsafe else 0.0))
        self.step_index += 1
        finished = self.step_index >= 24
        terminated = {agent: finished for agent in self.possible_agents}
        terminated["__all__"] = finished
        truncated = {agent: False for agent in self.possible_agents}
        truncated["__all__"] = False
        infos = {agent: {**outputs.get(agent, {}), "campus_peak_power_kw": total_power, "safety_violation": unsafe} for agent in self.possible_agents}
        if unsafe:
            for agent in self.possible_agents:
                terminated[agent] = True
            terminated["__all__"] = True
        return self._observations(), reward, terminated, truncated, infos


def rule_based_actions(env: CampusMultiAgentEnv) -> dict[str, np.ndarray]:
    """Zero-shift schedule preserving the synthetic reference setpoints."""
    return {agent: np.zeros(env.action_spaces[agent].shape, dtype=np.float32) for agent in env.possible_agents}


def independent_rule_actions(env: CampusMultiAgentEnv) -> dict[str, np.ndarray]:
    """Independent per-building zero-shift policies with a fixed safe coordinator."""
    return rule_based_actions(env)
