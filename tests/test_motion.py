"""Longitudinal physics and gear interlocks, independent of CAN publication."""

import pytest

from autocan.vehicle import MotionConfig, VehicleModel


def drive(vehicle: VehicleModel, gear: str = "D") -> None:
    vehicle.set_brake(100)
    assert vehicle.set_gear(gear)
    vehicle.set_brake(0)
    vehicle.set_throttle(100)


def test_park_preserves_the_original_free_rev_trajectory_without_moving() -> None:
    vehicle = VehicleModel()
    vehicle.set_throttle(50)
    vehicle.tick(100_000_000)
    assert vehicle.gear == "P"
    assert vehicle.brake_percent == 0
    assert vehicle.speed_kph == 0
    assert vehicle.rpm == 1000


@pytest.mark.parametrize(("gear", "speed", "rpm"), [("D", 10.26, 2010.4), ("R", -10.26, 2420.8)])
def test_drive_and_reverse_couple_speed_and_rpm(gear: str, speed: float, rpm: float) -> None:
    vehicle = VehicleModel()
    drive(vehicle, gear)
    vehicle.tick(1_000_000_000)
    assert vehicle.speed_kph == pytest.approx(speed)
    assert vehicle.rpm == pytest.approx(rpm)


def test_neutral_coasts_with_drag_even_with_full_throttle() -> None:
    vehicle = VehicleModel()
    drive(vehicle)
    vehicle.tick(1_000_000_000)
    assert vehicle.set_gear("N")
    vehicle.tick(1_000_000_000)
    assert vehicle.speed_kph == pytest.approx(9.72)


@pytest.mark.parametrize("gear", ["D", "R"])
def test_full_brake_overpowers_full_throttle_and_cannot_reverse_motion(gear: str) -> None:
    vehicle = VehicleModel()
    drive(vehicle, gear)
    vehicle.tick(1_000_000_000)
    vehicle.set_brake(100)
    vehicle.tick(1_000_000_000)
    assert vehicle.speed_kph == 0
    vehicle.tick(1_000_000_000)
    assert vehicle.speed_kph == 0


def test_engine_stop_removes_drive_but_allows_coasting_and_braking() -> None:
    vehicle = VehicleModel()
    drive(vehicle)
    vehicle.tick(1_000_000_000)
    vehicle.set_engine_running(False)
    vehicle.tick(1_000_000_000)
    assert vehicle.speed_kph == pytest.approx(9.72)
    vehicle.set_brake(100)
    vehicle.tick(1_000_000_000)
    assert vehicle.speed_kph == 0


def test_shift_rejection_does_not_change_speed_or_current_gear() -> None:
    vehicle = VehicleModel()
    assert not vehicle.set_gear("D")
    assert vehicle.shift_rejection == "brake_required"
    assert vehicle.gear == "P"
    drive(vehicle)
    vehicle.tick(1_000_000_000)
    before = vehicle.speed_kph
    assert not vehicle.set_gear("R")
    assert vehicle.shift_rejection == "vehicle_moving"
    assert vehicle.gear == "D"
    assert vehicle.speed_kph == before
    assert vehicle.set_gear("N")
    vehicle.set_brake(100)
    vehicle.tick(1_000_000_000)
    assert vehicle.set_gear("R")
    vehicle.set_brake(0)
    assert vehicle.set_gear("R")  # Idempotence does not require pressing brake again.
    assert vehicle.shift_rejection is None


def test_directional_speed_limits_are_enforced() -> None:
    vehicle = VehicleModel()
    drive(vehicle)
    vehicle.tick(100_000_000_000)
    assert vehicle.speed_kph == 180
    vehicle.set_brake(100)
    vehicle.tick(100_000_000_000)
    assert vehicle.speed_kph == 0
    drive(vehicle, "R")
    vehicle.tick(100_000_000_000)
    assert vehicle.speed_kph == -30


@pytest.mark.parametrize(("duration_ns", "accepted"), [(40_000_000, True), (50_000_000, False)])
def test_parking_interlock_checks_the_stationary_speed_boundary(
    duration_ns: int, accepted: bool
) -> None:
    vehicle = VehicleModel()
    drive(vehicle)
    vehicle.tick(duration_ns)
    vehicle.set_brake(100)
    assert vehicle.set_gear("P") is accepted
    assert vehicle.gear == ("P" if accepted else "D")


@pytest.mark.parametrize(("brake", "accepted"), [(9.99, False), (10.0, True)])
def test_gear_selection_requires_the_minimum_brake_input(brake: float, accepted: bool) -> None:
    vehicle = VehicleModel()
    vehicle.set_brake(brake)
    assert vehicle.set_gear("D") is accepted


def test_configured_speed_limit_changes_actual_motion() -> None:
    vehicle = VehicleModel(motion=MotionConfig(max_forward_kph=3.6))
    drive(vehicle)
    vehicle.tick(1_000_000_000)
    assert vehicle.speed_kph == 3.6


@pytest.mark.parametrize(
    "values",
    [
        {"max_forward_kph": float("nan")},
        {"max_reverse_kph": True},
        {"drive_acceleration_mps2": 0},
        {"brake_deceleration_mps2": 2},
        {"rolling_resistance_mps2": -1},
        {"shift_min_brake_percent": 101},
        {"shift_max_speed_kph": -1},
        {"drive_rpm_per_kph": 0},
    ],
)
def test_motion_configuration_rejects_invalid_or_unsafe_bounds(values: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        MotionConfig(**values)


@pytest.mark.parametrize("value", [-1, 101, float("nan"), float("inf"), True, "50", None])
def test_invalid_brake_is_rejected_before_mutating_input(value: object) -> None:
    vehicle = VehicleModel()
    with pytest.raises(ValueError):
        vehicle.set_brake(value)  # type: ignore[arg-type]
    assert vehicle.brake_percent == 0


@pytest.mark.parametrize("value", ["d", "X", 1, True, None, []])
def test_invalid_gear_is_rejected_before_mutating_selection(value: object) -> None:
    vehicle = VehicleModel()
    with pytest.raises(ValueError):
        vehicle.set_gear(value)  # type: ignore[arg-type]
    assert vehicle.gear == "P"
