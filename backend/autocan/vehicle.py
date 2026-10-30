"""A deliberately simple, noise-free RPM model, not an engine emulator."""

import math

from autocan.clock import validate_time


def validate_throttle(percent: float) -> None:
    if isinstance(percent, bool) or not math.isfinite(percent) or not 0 <= percent <= 100:
        raise ValueError("throttle must be finite and in [0, 100]")


def validate_ignition(state: str) -> None:
    if not isinstance(state, str) or state not in {"OFF", "ACC", "ON", "START"}:
        raise ValueError("ignition must be OFF, ACC, ON or START")


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
        self._engine_running = True
        self._ignition_state = "ON"

    @property
    def rpm(self) -> float:
        return self._rpm

    @property
    def throttle_percent(self) -> float:
        return self._throttle

    @property
    def engine_running(self) -> bool:
        return self._engine_running

    @property
    def ignition_state(self) -> str:
        return self._ignition_state

    def set_throttle(self, percent: float) -> None:
        validate_throttle(percent)
        self._throttle = float(percent)

    def set_engine_running(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        self._engine_running = enabled
        if enabled:
            self._ignition_state = "ON"

    def set_ignition(self, state: str) -> None:
        validate_ignition(state)
        self._ignition_state = "ON" if state == "START" else state
        if state in {"OFF", "ACC"}:
            self._engine_running = False
        elif state == "START":
            self._engine_running = True

    def tick(self, delta_ns: int) -> None:
        validate_time(delta_ns)
        target = (
            self._idle_rpm + (self._max_rpm - self._idle_rpm) * self._throttle / 100
            if self._engine_running
            else 0
        )
        limit = self._slew * delta_ns / 1_000_000_000
        self._rpm += max(-limit, min(limit, target - self._rpm))
