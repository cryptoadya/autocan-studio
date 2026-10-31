"""M6 faults alter emitted observations, never bypassing the CAN receiver."""

import json
from pathlib import Path

import pytest

from autocan.dbc import load_democar_database
from autocan.events import RunEvent
from autocan.scenarios import Scenario, ScenarioCommand
from autocan.simulation import Simulation
from autocan.traces import TraceRecorder, playback, record_scenario, rerun
from autocan.validation import object_value


def fault_command(kind: str, *, at: int = 0, **parameters: object) -> ScenarioCommand:
    spec: dict[str, object] = {
        "fault_id": "test-fault",
        "type": kind,
        "target_ecu": "engine",
        "target_message_id": 512,
        "parameters": {},
        **parameters,
    }
    return ScenarioCommand.from_dict({"at_ns": at, "kind": "inject_fault", "fault": spec})


def clear_command(at: int = 100_000_000) -> ScenarioCommand:
    return ScenarioCommand.from_dict({"at_ns": at, "kind": "clear_fault", "fault_id": "test-fault"})


@pytest.mark.parametrize(
    ("kind", "parameters", "expected"),
    [
        ("signal_spike", {"value": 7000}, 7000),
        ("signal_stuck", {}, 800),
        ("signal_bias", {"offset": 100, "rate_per_second": 1000}, 1140),
    ],
)
def test_signal_faults_change_can_rpm_and_clear_restores_sensor(
    kind: str, parameters: dict[str, object], expected: int
) -> None:
    command = fault_command(kind, signal="EngineRpm", parameters=parameters)
    with Simulation(profile_id="democar-network") as sim:
        sim.set_throttle(50, at_ns=0)
        command.schedule(sim)
        clear_command().schedule(sim)
        sim.advance_to(80_000_000)
        assert sim.state()["kombi_rpm"] == expected
        assert sim.state()["physical_rpm"] == 960
        sim.advance_to(100_000_000)
        assert sim.state()["kombi_rpm"] == sim.state()["physical_rpm"] == 1000


def test_all_receiver_drops_cause_timeout_without_stopping_other_messages() -> None:
    command = fault_command("message_drop", parameters={"probability": 1})
    with Simulation(profile_id="democar-network") as sim:
        command.schedule(sim)
        sim.advance_to(200_000_000)
        assert sim.state()["kombi_status"] == "TIMEOUT"
        assert sim.state()["kombi_rpm"] is None
        assert sim.state()["kombi_temperature_status"] == "FRESH"
        assert sim.state()["engine_running"] is True


def test_delay_reorders_exact_frames_and_clear_does_not_cancel_inflight() -> None:
    events: list[RunEvent] = []
    command = fault_command(
        "message_delay", parameters={"min_ns": 100_000_000, "max_ns": 100_000_000}
    )
    with Simulation(profile_id="democar-network", on_event=events.append) as sim:
        sim.set_throttle(50, at_ns=0)
        command.schedule(sim)
        clear_command(20_000_000).schedule(sim)
        sim.advance_to(80_000_000)
        assert sim.state()["kombi_rpm"] == 960
        sim.advance_to(100_000_000)
    published = {e.data["frame_id"]: e for e in events if e.event_kind == "FramePublished"}
    received = [
        e
        for e in events
        if e.event_kind == "FrameReceived"
        and e.data["receiver_id"] == "KOMBI"
        and published[e.data["frame_id"]].data["can_id"] == 512
    ]
    assert [published[e.data["frame_id"]].sim_time_ns for e in received] == [
        20_000_000,
        40_000_000,
        60_000_000,
        80_000_000,
        0,
        100_000_000,
    ]
    assert received[-2].sim_time_ns == 100_000_000
    assert published[received[-2].data["frame_id"]].data["payload_hex"] == "800c"


def test_frequency_override_applies_to_next_interval_and_clear_restores_base() -> None:
    events: list[RunEvent] = []
    command = fault_command("message_frequency_anomaly", parameters={"period_ns": 50_000_000})
    with Simulation(profile_id="democar-network", on_event=events.append) as sim:
        command.schedule(sim)
        clear_command(100_000_000).schedule(sim)
        sim.advance_to(140_000_000)
    assert [
        e.sim_time_ns
        for e in events
        if e.event_kind == "FramePublished" and e.data["can_id"] == 512
    ] == [0, 50_000_000, 100_000_000, 120_000_000, 140_000_000]


def test_cross_signal_fault_is_a_wheel_measurement_residual() -> None:
    command = fault_command(
        "cross_signal_inconsistency",
        target_ecu="abs",
        target_message_id=528,
        signal="FrontLeft",
        parameters={"offset": 40},
    )
    with Simulation(profile_id="democar-network") as sim:
        command.schedule(sim)
        sim.advance_to(0)
        assert sim.state()["physical_speed_kph"] == 0
        assert sim.state()["kombi_speed_kph"] == 10
        clear_command(20_000_000).schedule(sim)
        sim.advance_to(20_000_000)
        assert sim.state()["kombi_speed_kph"] == 0


def test_simultaneous_signal_faults_compose_in_activation_order() -> None:
    spike = fault_command("signal_spike", signal="EngineRpm", parameters={"value": 6000})
    bias = fault_command(
        "signal_bias", fault_id="bias", signal="EngineRpm", parameters={"offset": 100}
    )
    with Simulation(profile_id="democar-network") as sim:
        spike.schedule(sim)
        bias.schedule(sim)
        clear_command(20_000_000).schedule(sim)
        sim.advance_to(0)
        assert sim.state()["kombi_rpm"] == 6100
        sim.advance_to(20_000_000)
        assert sim.state()["kombi_rpm"] == 900
        assert sim.state()["active_fault_count"] == 1
        sim.reset()
        sim.advance_to(0)
        assert sim.state()["kombi_rpm"] == 800
        assert sim.state()["active_fault_count"] == 0


def test_overlapping_offline_faults_and_manual_online_are_independent() -> None:
    with Simulation(profile_id="democar-network") as sim:
        fault_command("ecu_offline", target_ecu="abs", target_message_id=None).schedule(sim)
        fault_command(
            "ecu_offline", fault_id="second", target_ecu="abs", target_message_id=None
        ).schedule(sim)
        sim.set_ecu_publishing("abs", False, at_ns=0)
        sim.set_ecu_publishing("abs", True, at_ns=20_000_000)
        clear_command(20_000_000).schedule(sim)
        sim.advance_to(200_000_000)
        assert sim.state()["abs_frames_published"] == 0
        ScenarioCommand.from_dict(
            {"at_ns": 220_000_000, "kind": "clear_fault", "fault_id": "second"}
        ).schedule(sim)
        sim.advance_to(220_000_000)
        assert sim.state()["kombi_speed_status"] == "FRESH"


def test_invalid_runtime_bias_is_an_explicit_dbc_error() -> None:
    with Simulation(profile_id="democar-network") as sim:
        fault_command("signal_bias", signal="EngineRpm", parameters={"offset": 8000}).schedule(sim)
        with pytest.raises(ValueError, match="DBC|range|8000"):
            sim.advance_to(0)


@pytest.mark.parametrize(
    "changes",
    [
        {"type": "raw_corruption"},
        {"target_ecu": "body"},
        {"target_message_id": 999},
        {"signal": "EngineOn"},
        {"parameters": {"value": 8000.25}},
        {"parameters": {"value": True}},
        {"parameters": {"value": float("nan")}},
        {"parameters": {"value": 6000, "extra": 1}},
        {"seed": True},
        {"fault_id": ""},
    ],
)
def test_fault_schema_rejects_invalid_targets_and_unrepresentable_values(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        spec = {
            "fault_id": "invalid",
            "type": "signal_spike",
            "target_ecu": "engine",
            "target_message_id": 512,
            "signal": "EngineRpm",
            "parameters": {"value": 6000},
        }
        ScenarioCommand.from_dict({"at_ns": 0, "kind": "inject_fault", "fault": spec | changes})


def test_manual_fault_commands_are_saved_separately_and_rerun_without_scenario(
    tmp_path: Path,
) -> None:
    path = tmp_path / "manual"
    with TraceRecorder(path) as recorder:
        with Simulation(profile_id="democar-network", on_event=recorder.write) as sim:
            recorder.begin(sim)
            fault_command("signal_stuck", signal="EngineRpm", parameters={}).schedule(sim)
            sim.set_throttle(50, at_ns=0)
            sim.advance_to(80_000_000)
            clear_command().schedule(sim)
            sim.advance_to(100_000_000)
            recorder.finish(sim.state())
    records = list(playback(path))
    assert sum(e["event_kind"] == "FaultActivated" for e in records) == 1
    assert sum(e["event_kind"] == "FaultCleared" for e in records) == 1
    assert "signal_stuck" not in (path / "events.jsonl").read_text()
    assert "inject_fault" in (path / "commands.jsonl").read_text()
    assert rerun(path)["matches"] is True


def test_random_delivery_faults_repeat_without_changing_unrelated_payloads(tmp_path: Path) -> None:
    commands = (
        fault_command("message_drop", parameters={"probability": 0.4}, seed=13),
        fault_command(
            "message_delay",
            fault_id="jitter",
            parameters={"min_ns": 0, "max_ns": 90_000_000},
            seed=14,
        ),
    )
    hashes = []
    for name in ("first", "second"):
        path = tmp_path / name
        meta = record_scenario(
            Scenario(400_000_000, commands, seed=42, profile_id="democar-network"), path
        )
        hashes.append(meta["semantic_sha256"])
        assert rerun(path)["matches"] is True
    assert hashes[0] == hashes[1]


def test_duplicate_inject_and_unknown_clear_have_replayable_rejections(tmp_path: Path) -> None:
    command = fault_command("signal_spike", signal="EngineRpm", parameters={"value": 6000})
    path = tmp_path / "rejections"
    record_scenario(
        Scenario(100_000_000, (clear_command(0), command, command), profile_id="democar-network"),
        path,
    )
    commands = [json.loads(line) for line in (path / "commands.jsonl").read_text().splitlines()]
    assert [c["accepted"] for c in commands] == [False, True, False]
    assert rerun(path)["matches"] is True


def test_faults_require_network_profile() -> None:
    scenario = {
        "schema_version": 1,
        "duration_ns": 0,
        "commands": [
            {
                "at_ns": 0,
                "kind": "inject_fault",
                "fault": {
                    "fault_id": "offline",
                    "type": "ecu_offline",
                    "target_ecu": "engine",
                    "target_message_id": None,
                    "parameters": {},
                },
            }
        ],
    }
    with pytest.raises(ValueError, match="network"):
        Scenario.from_dict(scenario)


def test_spike_frame_uses_strict_valid_dbc_payload() -> None:
    events: list[RunEvent] = []
    with Simulation(profile_id="democar-network", on_event=events.append) as sim:
        fault_command("signal_spike", signal="EngineRpm", parameters={"value": 7000}).schedule(sim)
        sim.advance_to(0)
    frame = next(e for e in events if e.event_kind == "FramePublished" and e.data["can_id"] == 512)
    assert frame.data["payload_hex"] == "606d"
    message = load_democar_database("democar-network").get_message_by_name("EngineStatus")
    assert (
        message.decode_simple(bytes.fromhex(str(frame.data["payload_hex"])), decode_choices=False)[
            "EngineRpm"
        ]
        == 7000
    )


@pytest.mark.parametrize(
    ("kind", "parameters"),
    [
        ("message_drop", {"probability": -0.1}),
        ("message_drop", {"probability": 1.1}),
        ("message_delay", {"min_ns": 20, "max_ns": 10}),
        ("message_delay", {"min_ns": 0, "max_ns": 1_000_000_001}),
        ("message_delay", {"min_ns": False, "max_ns": 20}),
        ("message_delay", {"min_ns": 0, "max_ns": 20.0}),
        ("message_frequency_anomaly", {"period_ns": 0}),
        ("message_frequency_anomaly", {"period_ns": 15_000_000}),
        ("message_frequency_anomaly", {"period_ns": 5_010_000_000}),
        ("signal_bias", {"offset": float("inf")}),
        ("ecu_offline", {"ignored": 1}),
    ],
)
def test_timing_and_probability_validation_rejects_invalid_parameters(
    kind: str, parameters: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        if kind == "signal_bias":
            fault_command(kind, parameters=parameters, signal="EngineRpm")
        else:
            fault_command(kind, parameters=parameters)


def test_activation_quota_rejects_without_disrupting_existing_faults(tmp_path: Path) -> None:
    commands = tuple(
        fault_command("message_drop", fault_id=f"drop-{i}", parameters={"probability": 0})
        for i in range(33)
    )
    path = tmp_path / "quota"
    meta = record_scenario(Scenario(0, commands, profile_id="democar-network"), path)
    assert object_value(meta["final_state"], "final state")["active_fault_count"] == 32
    applied = [e for e in playback(path) if e["event_kind"] == "InputApplied"]
    assert [e["accepted"] for e in applied] == [True] * 32 + [False]
    assert rerun(path)["matches"] is True


def test_last_frequency_override_clear_reveals_earlier_override() -> None:
    events: list[RunEvent] = []
    with Simulation(profile_id="democar-network", on_event=events.append) as sim:
        fault_command("message_frequency_anomaly", parameters={"period_ns": 40_000_000}).schedule(
            sim
        )
        fault_command(
            "message_frequency_anomaly", fault_id="last", parameters={"period_ns": 60_000_000}
        ).schedule(sim)
        ScenarioCommand.from_dict(
            {"at_ns": 60_000_000, "kind": "clear_fault", "fault_id": "last"}
        ).schedule(sim)
        sim.advance_to(140_000_000)
    assert [
        e.sim_time_ns
        for e in events
        if e.event_kind == "FramePublished" and e.data["can_id"] == 512
    ] == [0, 60_000_000, 100_000_000, 140_000_000]


def test_reset_replays_fault_rng_and_clears_inflight_frames() -> None:
    events: list[RunEvent] = []
    command = fault_command("message_delay", parameters={"min_ns": 0, "max_ns": 80_000_000}, seed=7)
    with Simulation(profile_id="democar-network", on_event=events.append) as sim:
        command.schedule(sim)
        sim.advance_to(100_000_000)
        first = [{k: v for k, v in e.as_dict().items() if k != "run_id"} for e in events]
        sim.reset()
        events.clear()
        command.schedule(sim)
        sim.advance_to(100_000_000)
        second = [{k: v for k, v in e.as_dict().items() if k != "run_id"} for e in events]
        assert first == second


def test_fault_randomness_is_independent_and_publication_physics_unchanged() -> None:
    observations = []
    publications = []
    for other in (False, True):
        events: list[RunEvent] = []
        with Simulation(profile_id="democar-network", on_event=events.append, seed=42) as sim:
            sim.set_throttle(50, at_ns=0)
            fault_command("message_drop", parameters={"probability": 0.4}, seed=13).schedule(sim)
            if other:
                fault_command(
                    "message_delay",
                    fault_id="unrelated",
                    target_ecu="abs",
                    target_message_id=528,
                    parameters={"min_ns": 0, "max_ns": 90_000_000},
                ).schedule(sim)
            sim.advance_to(400_000_000)
            assert sim.state()["physical_rpm"] == 1600
        engine_frames = {
            e.data["frame_id"]
            for e in events
            if e.event_kind == "FramePublished" and e.data["can_id"] == 512
        }
        observations.append(
            [
                (e.sim_time_ns, e.data["frame_id"], e.data["receiver_id"])
                for e in events
                if e.event_kind == "FrameReceived" and e.data["frame_id"] in engine_frames
            ]
        )
        publications.append(
            [
                (e.sim_time_ns, e.data["payload_hex"])
                for e in events
                if e.event_kind == "FramePublished" and e.data["can_id"] == 512
            ]
        )
    assert observations[0] == observations[1]
    assert publications[0] == publications[1]


def test_delayed_network_queue_overflow_is_explicit() -> None:
    with Simulation(profile_id="democar-network", delivery_delay_ns=60_000_000_000) as sim:
        with pytest.raises(RuntimeError, match="queue is full"):
            sim.advance_to(5_000_000_000)
