import numpy as np
import pytest

from campus_twin.rl_env import CampusMultiAgentEnv, independent_rule_actions, rule_based_actions


def test_multi_agent_env_runs_synthetic_baseline_and_invalid_actions_are_rejected():
    env = CampusMultiAgentEnv({"building_ids": ["sr-block", "ak-block"], "peak_limit_kw": 10_000.0})
    observations, reset_info = env.reset(seed=7)
    assert set(observations) == set(env.possible_agents)
    assert all(info["evidence_status"] == "synthetic" for info in reset_info.values())
    actions = rule_based_actions(env)
    assert set(actions) == set(env.possible_agents)
    next_obs, rewards, terminated, truncated, info = env.step(actions)
    assert set(next_obs) == set(env.possible_agents)
    assert set(rewards) == set(env.possible_agents)
    assert not terminated["__all__"]
    assert not truncated["__all__"]
    assert all(item["evidence_status"] == "simulated" for key, item in info.items() if key.startswith("building:"))
    assert independent_rule_actions(env).keys() == actions.keys()

    invalid = rule_based_actions(env)
    invalid["building:sr-block"] = np.array([np.nan, 0.0], dtype=np.float32)
    with pytest.raises(ValueError, match="non-finite"):
        env.step(invalid)

    invalid = rule_based_actions(env)
    invalid["building:sr-block"] = np.array([1.5, 0.0], dtype=np.float32)
    with pytest.raises(ValueError, match="out-of-range"):
        env.step(invalid)
