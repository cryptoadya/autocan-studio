"""Hand-counted episode metrics and splits catch credit/exposure leakage."""

from typing import cast

import pytest

from autocan.factory import FactoryManifest, RunSummary


def collection() -> FactoryManifest:
    runs: list[RunSummary] = [
        {
            "index": i,
            "path": f"runs/{i:04d}",
            "run_id": f"run-{i}",
            "mode": "idle",
            "seed": i,
            "duration_ns": 10_000,
            "fault_type": None,
            "scenario_sha256": f"scenario-{i}",
            "trace_semantic_sha256": f"trace-{i}",
            "observation_semantic_sha256": f"observed-{i}",
        }
        for i in range(10)
    ]
    return cast(FactoryManifest, {"semantic_sha256": "dataset", "runs": runs})


def test_whole_run_split_is_repeatable_disjoint_and_keeps_duplicate_inputs_together() -> None:
    from autocan.evaluation import make_split, validate_split

    meta = collection()
    meta["runs"][1]["observation_semantic_sha256"] = "observed-0"
    meta["runs"][2]["scenario_sha256"] = "scenario-1"  # transitive group 0, 1, 2
    split = make_split(meta, seed=42)
    assert split == make_split(meta, seed=42)
    assert split != make_split(meta, seed=43)
    assignments = split["assignments"]
    assert {row["index"] for row in assignments} == set(range(10))
    assert len(assignments) == 10
    assert len({row["split"] for row in assignments[:3]}) == 1
    assert {row["split"] for row in assignments} == {"train", "validation", "test"}
    validate_split(meta, split)
    invalid = {**split, "assignments": [*assignments, assignments[0]]}
    with pytest.raises(ValueError, match="split"):
        validate_split(meta, invalid)
    reused = collection()
    reused["runs"][1]["run_id"] = "run-0"
    with pytest.raises(ValueError, match="run"):
        make_split(reused, seed=42)
    for row in meta["runs"]:
        row["observation_semantic_sha256"] = "same"
    with pytest.raises(ValueError, match="groups"):
        make_split(meta, seed=42)


def test_one_to_one_matching_excludes_preexisting_unrelated_and_out_of_grace_alerts() -> None:
    from autocan.evaluation import AlertEpisode, FaultEpisode, evaluate_rule, summarize

    faults = [
        FaultEpisode("a", "signal_spike", 100, 200, (512,)),
        FaultEpisode("b", "signal_spike", 200, 300, (512,)),
    ]
    alarms = [
        AlertEpisode("rpm_derivative", 90, None, (512,)),  # preexisting, no credit
        AlertEpisode("rpm_derivative", 150, 160, (528,)),  # unrelated CAN ID
        AlertEpisode("rpm_derivative", 200, 210, (512,)),  # grace for a
        AlertEpisode("rpm_derivative", 210, 220, (512,)),  # b, not a again
        AlertEpisode("rpm_derivative", 350, None, (512,)),  # exact excluded upper bound
    ]
    result = evaluate_rule(faults, alarms, grace_ns=50, normal_duration_ns=0)
    assert [(m.fault_index, m.alert_index, m.latency_ns) for m in result.matches] == [
        (0, 2, 100),
        (1, 3, 10),
    ]
    metrics = summarize([result])
    assert metrics["detected_episodes"] == 2
    assert metrics["false_positive_episodes"] == 3
    assert metrics["detection_rate"] == 1
    assert metrics["episode_precision"] == 0.4
    assert metrics["false_alarms_per_hour"] is None
    assert metrics["detection_latency_ns"] == {"mean": 55, "p50": 10, "p95": 100}
    assert metrics["by_fault_type"]["signal_spike"]["detection_rate"] == 1


def test_normal_false_alarms_use_total_exposure_not_mean_of_rates_or_fault_run_time() -> None:
    from autocan.evaluation import AlertEpisode, FaultEpisode, evaluate_rule, summarize

    alarm = AlertEpisode("wheel_residual", 1, None, (528,))
    one_hour = 3_600_000_000_000
    normal = evaluate_rule([], [alarm], grace_ns=0, normal_duration_ns=one_hour // 2)
    quiet = evaluate_rule([], [], grace_ns=0, normal_duration_ns=one_hour * 3 // 2)
    faulty = evaluate_rule(
        [FaultEpisode("a", "signal_bias", 2, 10, (528,))], [alarm], grace_ns=0, normal_duration_ns=0
    )
    metrics = summarize([normal, quiet, faulty])
    assert metrics["false_alarms_per_hour"] == 0.5
    assert metrics["normal_false_alarm_episodes"] == 1
    assert metrics["false_positive_episodes"] == 2
    assert metrics["normal_duration_ns"] == 2 * one_hour
    assert metrics["detection_rate"] == 0
    empty = summarize([])
    assert empty["detection_rate"] is None and empty["episode_precision"] is None
    assert empty["false_alarms_per_hour"] is None
    assert empty["detection_latency_ns"] == {"mean": None, "p50": None, "p95": None}


def test_message_alerts_merge_per_rule_without_false_close_or_duplicate_episode() -> None:
    from autocan.evaluation import AlertCollector

    collector = AlertCollector()

    def alert(can_id: int, state: str) -> dict[str, object]:
        return {"kind": "alert", "rule_id": "heartbeat_timeout", "can_id": can_id, "state": state}

    collector.consume(100, [alert(512, "opened"), alert(528, "opened")])
    collector.consume(200, [alert(512, "resolved")])
    collector.consume(300, [alert(528, "resolved"), alert(529, "opened")])
    collector.consume(400, [])
    episodes = collector.finish()["heartbeat_timeout"]
    assert len(episodes) == 1
    assert (episodes[0].start_ns, episodes[0].end_ns, episodes[0].can_ids) == (
        100,
        None,
        (512, 528, 529),
    )


def test_resolved_episode_and_multiple_fault_types_have_separate_counts() -> None:
    from autocan.evaluation import AlertCollector, FaultEpisode, evaluate_rule, summarize

    collector = AlertCollector()
    collector.consume(
        10, [{"kind": "alert", "rule_id": "rpm_derivative", "can_id": 512, "state": "opened"}]
    )
    collector.consume(
        20, [{"kind": "alert", "rule_id": "rpm_derivative", "can_id": 512, "state": "resolved"}]
    )
    alarm = collector.finish()["rpm_derivative"][0]
    assert alarm.end_ns == 20
    faults = [
        FaultEpisode("a", "signal_spike", 5, 15, (512,)),
        FaultEpisode("b", "ecu_offline", 30, 40, (528,)),
    ]
    metrics = summarize([evaluate_rule(faults, [alarm], grace_ns=0, normal_duration_ns=0)])
    assert metrics["detection_rate"] == 0.5
    assert metrics["by_fault_type"]["signal_spike"]["detected_episodes"] == 1
    assert metrics["by_fault_type"]["ecu_offline"]["detected_episodes"] == 0


def test_later_target_message_does_not_inherit_earlier_unrelated_onset() -> None:
    from autocan.evaluation import AlertCollector, FaultEpisode, evaluate_rule

    collector = AlertCollector()
    collector.consume(
        100, [{"kind": "alert", "rule_id": "heartbeat_timeout", "can_id": 512, "state": "opened"}]
    )
    collector.consume(
        200, [{"kind": "alert", "rule_id": "heartbeat_timeout", "can_id": 528, "state": "opened"}]
    )
    alarms = collector.finish()["heartbeat_timeout"]
    expired = FaultEpisode("short", "ecu_offline", 90, 130, (528,))
    assert not evaluate_rule([expired], alarms, grace_ns=50, normal_duration_ns=0).matches
    live = FaultEpisode("long", "ecu_offline", 90, 300, (528,))
    result = evaluate_rule([live], alarms, grace_ns=50, normal_duration_ns=0)
    assert result.matches[0].latency_ns == 110
