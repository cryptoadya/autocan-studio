"""A deliberately simple, noise-free RPM model, not an engine emulator."""

import math

from autocan.clock import validate_time


def validate_throttle(percent: float) -> None:
    if isinstance(percent, bool) or not math.isfinite(percent) or not 0 <= percent <= 100:
        raise ValueError("throttle must be finite and in [0, 100]")


class VehicleModel:
    def __init__(
        self,
        *,
        initial_rpm: float = 800,
        idle_rpm: float = 800,
        max_rpm: float = 6000,
        slew_rpm_per_second: float = 2000,
    ) -> None:
        values = (initial_rpm, idle_rpm, max_rpm, slew_rpm_per_second)
        if any(isinstance(value, bool) or not math.isfinite(value) for value in values):
            raise ValueError("vehicle parameters must be finite numbers")
        if not 0 <= idle_rpm <= initial_rpm <= max_rpm <= 8000 or slew_rpm_per_second <= 0:
            raise ValueError("invalid RPM bounds or slew rate")
        self._rpm = float(initial_rpm)
        self._idle_rpm = idle_rpm
        self._max_rpm = max_rpm
        self._slew = slew_rpm_per_second
        self._throttle = 0.0

    @property
    def rpm(self) -> float:
        return self._rpm

    @property
    def throttle_percent(self) -> float:
        return self._throttle

    def set_throttle(self, percent: float) -> None:
        validate_throttle(percent)
        self._throttle = float(percent)

    def tick(self, delta_ns: int) -> None:
        validate_time(delta_ns)
        target = self._idle_rpm + (self._max_rpm - self._idle_rpm) * self._throttle / 100
        limit = self._slew * delta_ns / 1_000_000_000
        self._rpm += max(-limit, min(limit, target - self._rpm))
