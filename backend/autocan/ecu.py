"""Two independent ECU roles: a permitted RPM sensor and a CAN-only receiver."""

import math
from dataclasses import dataclass
from typing import Literal, Protocol

from cantools.database.errors import DecodeError

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
    status: Literal["UNAVAILABLE", "FRESH"] = "UNAVAILABLE"


class Kombi:
    def __init__(self) -> None:
        self._message = load_democar_database().get_message_by_name("EngineStatus")
        self._state = KombiState()

    @property
    def state(self) -> KombiState:
        return self._state

    def receive(self, observation: CanObservation) -> bool:
        if observation.channel != "democar" or observation.can_id != self._message.frame_id:
            return False
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
        self._state = KombiState(float(rpm), observation.received_at_ns, "FRESH")
        return True
