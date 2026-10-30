"""Queued motion controls must retain canonical ordering and state isolation."""

import pytest

from autocan.events import RunEvent
from autocan.simulation import Simulation


def queue_drive(sim: Simulation) -> None:
    sim.set_brake(100, at_ns=0)
    sim.set_gear("D", at_ns=0)
    sim.set_brake(0, at_ns=0)
    sim.set_throttle(100, at_ns=0)


def test_core_motion_changes_rpm_through_the_existing_can_chain() -> None:
    with Simulation() as sim:
        queue_drive(sim)
        sim.advance_to(1_000_000_000)
        state = sim.state()
        assert state["gear"] == "D"
        assert state["physical_speed_kph"] == pytest.approx(10.26)
        assert state["physical_rpm"] == pytest.approx(2010.4)
        assert state["kombi_rpm"] == 2010.5
        assert state["kombi_status"] == "FRESH"


def test_rejected_shift_is_logged_without_breaking_time_or_changing_gear() -> None:
    events: list[RunEvent] = []
    with Simulation(on_event=events.append) as sim:
        queue_drive(sim)
        sim.set_gear("R", at_ns=1_010_000_000)
        sim.advance_to(1_100_000_000)
        assert sim.state()["gear"] == "D"
        assert sim.state()["shift_rejection"] == "vehicle_moving"
        assert sim.state()["sim_time_ns"] == 1_100_000_000
    requests = [e for e in events if e.data.get("command_kind") == "set_gear"]
    assert [e.data["accepted"] for e in requests] == [True, False]
    assert requests[1].data["reason"] == "vehicle_moving"


def test_pause_reset_and_batch_order_preserve_the_motion_initial_state() -> None:
    with Simulation() as sim:
        sim.pause()
        queue_drive(sim)
        sim.advance_to(1_000_000_000)
        assert sim.state()["gear"] == "P"
        assert sim.state()["physical_speed_kph"] == 0
        sim.step()
        assert sim.state()["gear"] == "D"
        assert sim.state()["physical_speed_kph"] > 0  # type: ignore[operator]
        sim.reset()
        assert sim.state()["gear"] == "P"
        assert sim.state()["brake_percent"] == 0
        assert sim.state()["physical_speed_kph"] == 0
        assert sim.state()["shift_rejection"] is None


def test_drop_and_offline_do_not_change_the_motion_trajectory() -> None:
    paths: list[list[dict[str, object]]] = []
    for probability in (0.0, 0.5):
        events: list[RunEvent] = []
        with Simulation(seed=42, drop_probability=probability, on_event=events.append) as sim:
            queue_drive(sim)
            sim.set_engine_publishing(False, at_ns=500_000_000)
            sim.advance_to(1_000_000_000)
        paths.append(
            [
                {"time": e.sim_time_ns, "speed": e.data["physical_speed_kph"]}
                for e in events
                if e.event_kind == "PhysicalMotionUpdated"
            ]
        )
    assert len(paths[0]) == 100
    assert paths[0] == paths[1]
