"""The headless slice runs end to end and exposes measured RPM separately."""

import pytest


def test_physics_changes_do_not_update_kombi_until_a_frame_arrives() -> None:
    from autocan.simulation import Simulation

    with Simulation() as sim:
        sim.set_throttle(50)
        sim.advance_to(0)
        assert sim.state()["kombi_rpm"] == 800
        sim.advance_to(10_000_000)
        assert sim.state()["physical_rpm"] == 820
        assert sim.state()["kombi_rpm"] == 800
        sim.advance_to(20_000_000)
        assert sim.state()["kombi_rpm"] == 840


def test_two_30_second_runs_have_identical_canonical_events() -> None:
    from autocan.events import RunEvent
    from autocan.simulation import Simulation

    runs: list[list[dict[str, object]]] = []
    for _ in range(2):
        events: list[dict[str, object]] = []

        def collect(event: RunEvent, target: list[dict[str, object]] = events) -> None:
            record = event.as_dict()
            record.pop("run_id")
            target.append(record)

        with Simulation(seed=42, on_event=collect) as sim:
            sim.set_throttle(50)
            sim.set_throttle(0, at_ns=10_000_000_000)
            sim.advance_to(30_000_000_000)
            assert sim.state()["frames_published"] == 1501
            assert sim.state()["deliveries"] == 1501
            assert sim.state()["kombi_rpm"] == 800
        runs.append(events)
    assert runs[0] == runs[1]
    assert runs[0][0]["event_kind"] == "InputApplied"


def test_pause_step_reset_and_late_command_boundary() -> None:
    from autocan.simulation import Simulation

    with Simulation(seed=7) as sim:
        sim.advance_to(0)
        sim.pause()
        sim.set_throttle(50)
        sim.advance_to(100_000_000)
        assert sim.state()["sim_time_ns"] == 0
        sim.step()
        assert sim.state()["sim_time_ns"] == 10_000_000
        assert sim.state()["physical_rpm"] == 820
        assert sim.state()["paused"] is True
        sim.resume()
        sim.advance_to(20_000_000)
        assert sim.state()["kombi_rpm"] == 840
        sim.reset()
        assert sim.state()["kombi_rpm"] is None
        sim.advance_to(20_000_000)
        assert sim.state()["kombi_rpm"] == 800
        assert sim.state()["frames_published"] == 2


def test_invalid_command_leaves_state_and_event_stream_unchanged() -> None:
    from autocan.events import RunEvent
    from autocan.simulation import Simulation

    events: list[RunEvent] = []
    with Simulation(on_event=events.append) as sim:
        before = sim.state()
        with pytest.raises(ValueError):
            sim.set_throttle(float("nan"))
        assert sim.state() == before
        assert events == []


def test_same_time_commands_apply_in_logged_order_before_publication() -> None:
    from autocan.simulation import Simulation

    with Simulation() as sim:
        sim.set_throttle(100, at_ns=20_000_000)
        sim.set_throttle(0, at_ns=20_000_000)
        sim.advance_to(20_000_000)
        assert sim.state()["kombi_rpm"] == 800
