"""A deliberately simple, noise-free RPM model, not an engine emulator."""

import math
from dataclasses import dataclass

from autocan.clock import validate_time


def validate_throttle(percent: float) -> None:
    if isinstance(percent, bool) or not math.isfinite(percent) or not 0 <= percent <= 100:
        raise ValueError("throttle must be finite and in [0, 100]")


def validate_ignition(state: str) -> None:
    if not isinstance(state, str) or state not in {"OFF", "ACC", "ON", "START"}:
        raise ValueError("ignition must be OFF, ACC, ON or START")


def validate_brake(percent: float) -> None:
    if (
        isinstance(percent, bool)
        or not isinstance(percent, (int, float))
        or not math.isfinite(percent)
        or not 0 <= percent <= 100
    ):
        raise ValueError("brake must be finite and in [0, 100]")


def validate_gear(gear: str) -> None:
    if not isinstance(gear, str) or gear not in {"P", "R", "N", "D"}:
        raise ValueError("gear must be P, R, N or D")


@dataclass(frozen=True, slots=True)
class MotionConfig:
    max_forward_kph: float = 180
    max_reverse_kph: float = 30
    drive_acceleration_mps2: float = 3
    brake_deceleration_mps2: float = 8
    rolling_resistance_mps2: float = 0.15
    shift_max_speed_kph: float = 0.5
    shift_min_brake_percent: float = 10
    drive_rpm_per_kph: float = 40
    reverse_rpm_per_kph: float = 80
    throttle_rpm_per_percent: float = 8

    def __post_init__(self) -> None:
        values = (
            self.max_forward_kph,
            self.max_reverse_kph,
            self.drive_acceleration_mps2,
            self.brake_deceleration_mps2,
            self.rolling_resistance_mps2,
            self.shift_max_speed_kph,
            self.shift_min_brake_percent,
            self.drive_rpm_per_kph,
            self.reverse_rpm_per_kph,
            self.throttle_rpm_per_percent,
        )
        if any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in values
        ):
            raise ValueError("motion parameters must be finite numbers")
        if not (
            0 < self.max_forward_kph <= 300
            and 0 < self.max_reverse_kph <= 60
            and 0 < self.drive_acceleration_mps2 < self.brake_deceleration_mps2
            and self.rolling_resistance_mps2 >= 0
            and 0 <= self.shift_max_speed_kph <= 1
            and 0 <= self.shift_min_brake_percent <= 100
            and self.drive_rpm_per_kph > 0
            and self.reverse_rpm_per_kph > 0
            and self.throttle_rpm_per_percent >= 0
        ):
            raise ValueError("invalid speed/acceleration bounds; full brake must exceed drive")


class VehicleModel:
    def __init__(
        self,
        *,
        initial_rpm: float = 800,
        idle_rpm: float = 800,
        max_rpm: float = 6000,
        slew_rpm_per_second: float = 2000,
        motion: MotionConfig | None = None,
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
        self._motion_config = motion if motion is not None else MotionConfig()
        self._speed_mps = 0.0
        self._brake = 0.0
        self._gear = "P"
        self._shift_rejection: str | None = None

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

    @property
    def speed_kph(self) -> float:
        return round(self._speed_mps * 3.6, 9)

    @property
    def motion_config(self) -> MotionConfig:
        return self._motion_config

    @property
    def brake_percent(self) -> float:
        return self._brake

    @property
    def gear(self) -> str:
        return self._gear

    @property
    def shift_rejection(self) -> str | None:
        return self._shift_rejection

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

    def set_brake(self, percent: float) -> None:
        validate_brake(percent)
        self._brake = float(percent)

    def set_gear(self, gear: str) -> bool:
        validate_gear(gear)
        if gear != self._gear and gear != "N":
            if abs(self.speed_kph) > self.motion_config.shift_max_speed_kph:
                self._shift_rejection = "vehicle_moving"
                return False
            if self._brake < self.motion_config.shift_min_brake_percent:
                self._shift_rejection = "brake_required"
                return False
            self._speed_mps = 0.0
        self._gear = gear
        self._shift_rejection = None
        return True

    def _motion_tick(self, seconds: float) -> None:
        if self._gear == "P":
            self._speed_mps = 0.0
            return
        config = self.motion_config
        direction = 1 if self._gear == "D" else -1 if self._gear == "R" else 0
        drive = (
            direction * config.drive_acceleration_mps2 * self._throttle / 100
            if self._engine_running
            else 0.0
        )
        resistance = (
            config.brake_deceleration_mps2 * self._brake / 100 + config.rolling_resistance_mps2
        )
        before = self._speed_mps
        if before:
            acceleration = drive - math.copysign(resistance, before)
            after = before + acceleration * seconds
            if before * after < 0:
                after = 0.0
        else:
            after = math.copysign(max(0.0, abs(drive) - resistance) * seconds, drive)
        self._speed_mps = round(
            max(-config.max_reverse_kph / 3.6, min(config.max_forward_kph / 3.6, after)), 12
        )

    def tick(self, delta_ns: int) -> None:
        validate_time(delta_ns)
        self._motion_tick(delta_ns / 1_000_000_000)
        target = (
            self._idle_rpm + (self._max_rpm - self._idle_rpm) * self._throttle / 100
            if self._engine_running
            else 0
        )
        if self._engine_running and self._gear in {"D", "R"}:
            ratio = (
                self.motion_config.drive_rpm_per_kph
                if self._gear == "D"
                else self.motion_config.reverse_rpm_per_kph
            )
            target = min(
                self._max_rpm,
                self._idle_rpm
                + abs(self.speed_kph) * ratio
                + self._throttle * self.motion_config.throttle_rpm_per_percent,
            )
        limit = self._slew * delta_ns / 1_000_000_000
        self._rpm += max(-limit, min(limit, target - self._rpm))
