"""Two independent ECU roles: a permitted RPM sensor and a CAN-only receiver."""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from cantools.database.errors import DecodeError

from autocan.clock import Phase, SimulationClock
from autocan.dbc import load_democar_database
from autocan.events import CanFrame, CanObservation


class RpmSensorPort(Protocol):
    def read_rpm(self) -> float: ...


class EngineEcu:
    def __init__(self, sensor: RpmSensorPort) -> None:
        self._sensor = sensor
        self._message = load_democar_database().get_message_by_name("EngineStatus")

    def publish(self, *, frame_id: int, created_at_ns: int) -> CanFrame:
        rpm = self._sensor.read_rpm()
        if not math.isfinite(rpm):
            raise ValueError("RPM sensor returned a nonfinite value")
        payload = self._message.encode({"EngineRpm": rpm}, strict=True)
        return CanFrame(
            frame_id,
            "engine",
            "democar",
            self._message.frame_id,
            self._message.length,
            payload,
            created_at_ns,
        )


@dataclass(frozen=True, slots=True)
class KombiState:
    rpm: float | None = None
    last_received_ns: int | None = None
    status: Literal["UNAVAILABLE", "FRESH", "STALE", "TIMEOUT"] = "UNAVAILABLE"


class Kombi:
    def __init__(
        self,
        *,
        clock: SimulationClock | None = None,
        on_status: Callable[[KombiState], None] | None = None,
    ) -> None:
        self._message = load_democar_database().get_message_by_name("EngineStatus")
        self._state = KombiState()
        self._clock = clock
        self._on_status = on_status

    @property
    def state(self) -> KombiState:
        return self._state

    def receive(self, observation: CanObservation) -> bool:
        if observation.channel != "democar" or observation.can_id != self._message.frame_id:
            return False
        if self._clock is not None and observation.received_at_ns != self._clock.now_ns:
            raise ValueError("receive time must match the virtual clock")
        if (
            self._state.last_received_ns is not None
            and observation.received_at_ns < self._state.last_received_ns
        ):
            raise ValueError("out-of-order receiver time")
        try:
            signals = self._message.decode_simple(
                observation.payload, allow_truncated=False, allow_excess=False
            )
        except DecodeError as exc:
            raise ValueError("invalid EngineStatus payload") from exc
        rpm = signals["EngineRpm"]
        if not isinstance(rpm, (int, float)) or not math.isfinite(rpm) or not 0 <= rpm <= 8000:
            raise ValueError("received RPM is outside the profile range")
        self._update(KombiState(float(rpm), observation.received_at_ns, "FRESH"))
        if self._clock is not None:
            received = observation.received_at_ns

            def check() -> None:
                if self._state.last_received_ns != received:
                    return
                assert self._clock is not None
                age = self._clock.now_ns - received
                status: Literal["STALE", "TIMEOUT"] = "TIMEOUT" if age >= 200_000_000 else "STALE"
                self._update(KombiState(self._state.rpm, received, status))

            self._clock.schedule(received + 60_000_000, Phase.DEADLINE, check)
            self._clock.schedule(received + 200_000_000, Phase.DEADLINE, check)
        return True

    def _update(self, state: KombiState) -> None:
        previous = self._state.status
        self._state = state
        if previous != state.status and self._on_status is not None:
            self._on_status(state)
