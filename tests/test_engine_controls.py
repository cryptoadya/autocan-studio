"""Physical engine controls must not masquerade as loss of CAN publication."""

import pytest

from autocan.simulation import Simulation
from autocan.vehicle import VehicleModel


def test_stop_coasts_to_zero_despite_throttle_and_start_restores_idle() -> None:
    vehicle = VehicleModel()
    vehicle.set_throttle(100)
    vehicle.set_engine_running(False)
    vehicle.tick(200_000_000)
    assert vehicle.rpm == 400
    assert vehicle.engine_running is False
    assert vehicle.ignition_state == "ON"
    vehicle.tick(200_000_000)
    assert vehicle.rpm == 0
    vehicle.set_throttle(0)
    vehicle.set_engine_running(True)
    vehicle.tick(400_000_000)
    assert vehicle.rpm == 800
    assert vehicle.engine_running is True


@pytest.mark.parametrize(
    ("ignition_request", "ignition", "running"),
    [("OFF", "OFF", False), ("ACC", "ACC", False), ("ON", "ON", False), ("START", "ON", True)],
)
def test_ignition_on_does_not_start_a_stopped_engine(
    ignition_request: str, ignition: str, running: bool
) -> None:
    vehicle = VehicleModel()
    vehicle.set_engine_running(False)
    vehicle.set_ignition(ignition_request)
    assert vehicle.ignition_state == ignition
    assert vehicle.engine_running is running


@pytest.mark.parametrize("value", ["on", "RUN", "", 1, True, None, []])
def test_invalid_ignition_is_rejected_without_mutating_the_model(value: object) -> None:
    vehicle = VehicleModel()
    with pytest.raises(ValueError):
        vehicle.set_ignition(value)  # type: ignore[arg-type]
    assert vehicle.engine_running is True
    assert vehicle.ignition_state == "ON"
    assert vehicle.rpm == 800


def test_start_from_accessory_promotes_ignition_and_repeated_controls_are_idempotent() -> None:
    vehicle = VehicleModel()
    vehicle.set_ignition("ACC")
    vehicle.set_engine_running(True)
    vehicle.tick(10_000_000)
    vehicle.set_engine_running(True)
    assert vehicle.ignition_state == "ON"
    assert vehicle.engine_running is True
    assert vehicle.rpm == 800


def test_stopped_engine_keeps_publishing_fresh_zero_rpm() -> None:
    with Simulation() as sim:
        sim.set_engine_running(False, at_ns=0)
        sim.advance_to(400_000_000)
        state = sim.state()
        assert state["physical_rpm"] == state["kombi_rpm"] == 0
        assert state["engine_running"] is False
        assert state["engine_publishing"] is True
        assert state["kombi_status"] == "FRESH"
        assert state["frames_published"] == state["deliveries"] == 21


def test_offline_hides_both_stop_and_restart_until_new_can_delivery() -> None:
    with Simulation() as sim:
        sim.advance_to(20_000_000)
        sim.set_engine_publishing(False, at_ns=30_000_000)
        sim.set_engine_running(False, at_ns=30_000_000)
        sim.advance_to(500_000_000)
        assert sim.state()["physical_rpm"] == 0
        assert sim.state()["kombi_rpm"] == 800
        assert sim.state()["kombi_status"] == "TIMEOUT"
        sim.set_ignition("START", at_ns=510_000_000)
        sim.advance_to(1_000_000_000)
        assert sim.state()["engine_running"] is True
        assert sim.state()["kombi_status"] == "TIMEOUT"
        sim.set_engine_publishing(True, at_ns=1_010_000_000)
        sim.advance_to(1_020_000_000)
        assert sim.state()["kombi_status"] == "FRESH"
        assert sim.state()["kombi_rpm"] == 800


def test_paused_controls_wait_for_step_and_reset_restores_engine_and_ignition() -> None:
    with Simulation() as sim:
        sim.pause()
        sim.set_ignition("OFF")
        sim.advance_to(100_000_000)
        assert sim.state()["engine_running"] is True
        sim.step()
        assert sim.state()["engine_running"] is False
        assert sim.state()["ignition_state"] == "OFF"
        sim.reset()
        assert sim.state()["engine_running"] is True
        assert sim.state()["ignition_state"] == "ON"
        assert sim.state()["physical_rpm"] == 800


@pytest.mark.parametrize("value", [0, 1, "true", None])
def test_engine_running_requires_a_boolean_before_queuing(value: object) -> None:
    with Simulation() as sim:
        before = sim.state()
        with pytest.raises(ValueError):
            sim.set_engine_running(value)  # type: ignore[arg-type]
        assert sim.state() == before
