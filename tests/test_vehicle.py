"""A deterministic synthetic RPM model and its input boundary."""

import pytest


def test_throttle_moves_rpm_with_fixed_slew_then_returns_to_idle() -> None:
    from autocan.vehicle import VehicleModel

    vehicle = VehicleModel()
    vehicle.set_throttle(50)
    vehicle.tick(10_000_000)
    assert vehicle.rpm == 820
    for _ in range(9):
        vehicle.tick(10_000_000)
    assert vehicle.rpm == 1000
    vehicle.set_throttle(0)
    for _ in range(10):
        vehicle.tick(10_000_000)
    assert vehicle.rpm == 800


@pytest.mark.parametrize("throttle", [-1, 101, float("nan"), float("inf"), True])
def test_bad_throttle_does_not_mutate_vehicle(throttle: float) -> None:
    from autocan.vehicle import VehicleModel

    vehicle = VehicleModel()
    with pytest.raises(ValueError):
        vehicle.set_throttle(throttle)
    vehicle.tick(10_000_000)
    assert vehicle.rpm == 800
    assert vehicle.throttle_percent == 0


def test_saturates_at_target_without_overshoot() -> None:
    from autocan.vehicle import VehicleModel

    vehicle = VehicleModel()
    vehicle.set_throttle(100)
    vehicle.tick(10_000_000_000)
    assert vehicle.rpm == 6000
