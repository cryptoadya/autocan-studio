"""Five ECU with narrow sensor/actuator ports and CAN-only subscription state."""

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from cantools.database.errors import DecodeError

from autocan.clock import Phase, SimulationClock
from autocan.dbc import load_democar_database
from autocan.events import CanFrame, CanObservation, Scalar
from autocan.network_profile import (
    GEARS,
    IGNITIONS,
    MESSAGE_PLANS,
    PLAN_BY_ID,
    PLAN_BY_NAME,
    SHIFT_REASONS,
    STATUS_CODES,
)

type StatusCallback = Callable[[str, str, str], None]
type ShiftCallback = Callable[[int, str, str, bool, str | None], None]


class EngineSensors(Protocol):
    def read_rpm(self) -> float: ...
    def read_throttle(self) -> float: ...
    def read_coolant(self) -> float: ...
    def read_engine_running(self) -> bool: ...


class WheelSensors(Protocol):
    def read_wheel_speeds(self) -> tuple[float, float, float, float]: ...


class BodySensors(Protocol):
    def read_ignition(self) -> str: ...
    def read_throttle(self) -> float: ...
    def read_brake(self) -> float: ...
    def read_gear_request(self) -> tuple[str, int]: ...


class GearActuator(Protocol):
    def select_gear(self, gear: str) -> tuple[bool, str | None]: ...


@dataclass(frozen=True, slots=True)
class Reading:
    values: Mapping[str, float]
    received_at_ns: int | None = None
    status: str = "UNAVAILABLE"


class NetworkReceiver:
    def __init__(
        self, ecu_id: str, clock: SimulationClock, on_status: StatusCallback | None = None
    ) -> None:
        self.ecu_id = ecu_id
        self.clock = clock
        self._on_status = on_status
        self._database = load_democar_database("democar-network")
        self._readings = {
            p.name: Reading(MappingProxyType({})) for p in MESSAGE_PLANS if ecu_id in p.receivers
        }
        for name in self._readings:
            self._arm(name, None)

    def reading(self, name: str) -> Reading:
        return self._readings[name]

    def value(self, name: str, signal: str, default: float = 0) -> float:
        return self.reading(name).values.get(signal, default)

    def status(self, name: str) -> str:
        return self.reading(name).status

    def aggregate_status(self, names: tuple[str, ...]) -> str:
        states = {self.status(name) for name in names}
        return next((s for s in ("TIMEOUT", "STALE", "UNAVAILABLE") if s in states), "FRESH")

    def _replace(self, name: str, reading: Reading) -> None:
        before = self._readings[name].status
        self._readings[name] = reading
        if before != reading.status and self._on_status is not None:
            self._on_status(self.ecu_id, name, reading.status)

    def _arm(self, name: str, received: int | None) -> None:
        period = PLAN_BY_NAME[name].period_ns

        def check(status: str) -> None:
            current = self.reading(name)
            if current.received_at_ns == received:
                self._replace(name, Reading(current.values, received, status))

        if received is None:
            self.clock.schedule(period * 10, Phase.DEADLINE, lambda: check("TIMEOUT"))
        else:
            self.clock.schedule(received + period * 3, Phase.DEADLINE, lambda: check("STALE"))
            self.clock.schedule(received + period * 10, Phase.DEADLINE, lambda: check("TIMEOUT"))

    def receive(self, observation: CanObservation) -> bool:
        plan = PLAN_BY_ID.get(observation.can_id)
        if observation.channel != "democar" or plan is None or plan.name not in self._readings:
            return False
        if observation.received_at_ns != self.clock.now_ns:
            raise ValueError("receive time must match the virtual clock")
        message = self._database.get_message_by_name(plan.name)
        try:
            decoded = message.decode_simple(
                observation.payload, decode_choices=False, allow_truncated=False, allow_excess=False
            )
        except DecodeError as exc:
            raise ValueError(f"invalid {plan.name} payload") from exc
        values: dict[str, float] = {}
        for signal in message.signals:
            value = decoded[signal.name]
            if (
                not isinstance(value, (int, float))
                or not math.isfinite(value)
                or (signal.minimum is not None and value < signal.minimum)
                or (signal.maximum is not None and value > signal.maximum)
            ):
                raise ValueError(f"invalid {plan.name}.{signal.name}")
            values[signal.name] = float(value)
        self._replace(
            plan.name, Reading(MappingProxyType(values), observation.received_at_ns, "FRESH")
        )
        self._arm(plan.name, observation.received_at_ns)
        self._received(plan.name)
        return True

    def _received(self, name: str) -> None:
        pass

    def _signals(self, name: str) -> dict[str, float | int]:
        raise ValueError(f"{self.ecu_id} does not publish {name}")

    def publish(self, name: str, *, frame_id: int, created_at_ns: int) -> CanFrame:
        plan = PLAN_BY_NAME[name]
        if plan.source_ecu != self.ecu_id:
            raise ValueError("message publisher mismatch")
        message = self._database.get_message_by_name(name)
        payload = message.encode(self._signals(name), strict=True)
        return CanFrame(
            frame_id,
            self.ecu_id,
            "democar",
            message.frame_id,
            message.length,
            payload,
            created_at_ns,
        )


class EngineNode(NetworkReceiver):
    def __init__(
        self,
        clock: SimulationClock,
        sensors: EngineSensors,
        on_status: StatusCallback | None = None,
    ) -> None:
        super().__init__("engine", clock, on_status)
        self._sensors = sensors

    def _signals(self, name: str) -> dict[str, float | int]:
        if name == "EngineStatus":
            return {"EngineRpm": self._sensors.read_rpm()}
        return {
            "Throttle": self._sensors.read_throttle(),
            "CoolantTemperature": self._sensors.read_coolant(),
            "EngineOn": int(self._sensors.read_engine_running()),
            "BodyLink": STATUS_CODES[self.status("BodyState")],
            "ClusterLink": STATUS_CODES[self.status("ClusterHeartbeat")],
        }


class AbsNode(NetworkReceiver):
    def __init__(
        self, clock: SimulationClock, sensors: WheelSensors, on_status: StatusCallback | None = None
    ) -> None:
        super().__init__("abs", clock, on_status)
        self._sensors = sensors

    def _signals(self, name: str) -> dict[str, float | int]:
        wheels = self._sensors.read_wheel_speeds()
        if name == "WheelSpeeds":
            return dict(
                zip(("FrontLeft", "FrontRight", "RearLeft", "RearRight"), wheels, strict=True)
            )
        return {
            "BrakeObserved": self.value("DriverInput", "Brake"),
            "WheelsConsistent": int(max(wheels) - min(wheels) <= 0.5),
            "DriverLink": STATUS_CODES[self.status("DriverInput")],
            "BodyLink": STATUS_CODES[self.status("BodyState")],
        }


class BodyNode(NetworkReceiver):
    def __init__(
        self, clock: SimulationClock, sensors: BodySensors, on_status: StatusCallback | None = None
    ) -> None:
        super().__init__("body", clock, on_status)
        self._sensors = sensors

    def _signals(self, name: str) -> dict[str, float | int]:
        if name == "DriverInput":
            gear, sequence = self._sensors.read_gear_request()
            return {
                "Throttle": self._sensors.read_throttle(),
                "Brake": self._sensors.read_brake(),
                "RequestedGear": GEARS.index(gear),
                "RequestSequence": sequence,
            }
        return {
            "Ignition": IGNITIONS.index(self._sensors.read_ignition()),
            "EngineLink": STATUS_CODES[self.status("EngineTelemetry")],
            "ClusterLink": STATUS_CODES[self.status("ClusterHeartbeat")],
        }


class TransmissionNode(NetworkReceiver):
    REQUIRED = ("EngineStatus", "WheelSpeeds", "DriverInput", "BodyState")

    def __init__(
        self,
        clock: SimulationClock,
        actuator: GearActuator,
        on_status: StatusCallback | None = None,
        on_shift: ShiftCallback | None = None,
    ) -> None:
        super().__init__("transmission", clock, on_status)
        self._actuator = actuator
        self._on_shift = on_shift
        self._last_request: int | None = None
        self.gear = "P"
        self.accepted = True
        self.reason: str | None = None
        self.request_sequence = 0

    def _received(self, name: str) -> None:
        if name != "DriverInput":
            return
        sequence = int(self.value(name, "RequestSequence"))
        if sequence == self._last_request:
            return
        self._last_request = self.request_sequence = sequence
        requested = GEARS[int(self.value(name, "RequestedGear"))]
        reason: str | None = None
        if requested not in (self.gear, "N"):
            if self.aggregate_status(self.REQUIRED) != "FRESH":
                reason = "inputs_unavailable"
            elif max(abs(v) for v in self.reading("WheelSpeeds").values.values()) > 0.5:
                reason = "vehicle_moving"
            elif self.value("DriverInput", "Brake") < 10:
                reason = "brake_required"
        if reason is None:
            accepted, reason = self._actuator.select_gear(requested)
            if (
                type(accepted) is not bool
                or reason not in SHIFT_REASONS
                or (accepted and reason is not None)
            ):
                raise ValueError("invalid gear actuator result")
        else:
            accepted = False
        self.accepted, self.reason = accepted, reason
        if accepted:
            self.gear = requested
        if self._on_shift is not None:
            self._on_shift(sequence, requested, self.gear, accepted, reason)

    def _signals(self, name: str) -> dict[str, float | int]:
        return {
            "Gear": GEARS.index(self.gear),
            "ShiftAccepted": int(self.accepted),
            "ShiftReason": SHIFT_REASONS[self.reason],
            "InputsStatus": STATUS_CODES[self.aggregate_status(self.REQUIRED)],
            "ThrottleObserved": self.value("DriverInput", "Throttle"),
            "EngineRpmObserved": self.value("EngineStatus", "EngineRpm"),
            "RequestSequence": self.request_sequence,
        }


class NetworkKombi(NetworkReceiver):
    def __init__(self, clock: SimulationClock, on_status: StatusCallback | None = None) -> None:
        super().__init__("KOMBI", clock, on_status)
        self._alive = 0

    def _signals(self, name: str) -> dict[str, float | int]:
        return {
            "EngineLink": STATUS_CODES[self.status("EngineStatus")],
            "WheelsLink": STATUS_CODES[self.status("WheelSpeeds")],
            "TransmissionLink": STATUS_CODES[self.status("TransmissionState")],
            "BodyLink": STATUS_CODES[self.status("BodyState")],
            "AliveCounter": self._alive,
        }

    def publish(self, name: str, *, frame_id: int, created_at_ns: int) -> CanFrame:
        frame = super().publish(name, frame_id=frame_id, created_at_ns=created_at_ns)
        self._alive = (self._alive + 1) % 256
        return frame

    def display_state(self) -> dict[str, Scalar]:
        return kombi_display(self.reading)


def kombi_display(reading: Callable[[str], Reading]) -> dict[str, Scalar]:
    """The same pure CAN-reading projection is used for live and saved views."""
    rpm, wheels, telemetry = (
        reading("EngineStatus"),
        reading("WheelSpeeds"),
        reading("EngineTelemetry"),
    )
    transmission, body, abs_status = (
        reading("TransmissionState"),
        reading("BodyState"),
        reading("AbsStatus"),
    )
    return {
        "kombi_rpm": rpm.values.get("EngineRpm"),
        "kombi_status": rpm.status,
        "last_received_ns": rpm.received_at_ns,
        "kombi_speed_kph": sum(wheels.values.values()) / 4 if wheels.values else None,
        "kombi_speed_status": wheels.status,
        "kombi_temperature_c": telemetry.values.get("CoolantTemperature"),
        "kombi_temperature_status": telemetry.status,
        "kombi_engine_on": bool(telemetry.values["EngineOn"]) if telemetry.values else None,
        "kombi_gear": GEARS[int(transmission.values["Gear"])] if transmission.values else None,
        "kombi_gear_status": transmission.status,
        "kombi_ignition": IGNITIONS[int(body.values["Ignition"])] if body.values else None,
        "kombi_body_status": body.status,
        "kombi_wheels_consistent": bool(abs_status.values["WheelsConsistent"])
        if abs_status.values
        else None,
        "kombi_abs_status": abs_status.status,
    }
