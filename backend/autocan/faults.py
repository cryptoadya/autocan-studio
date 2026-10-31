"""Synthetic faults at the encoding, publication and receiver-delivery boundaries."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from random import Random
from types import MappingProxyType

from autocan.dbc import load_democar_database
from autocan.events import CanFrame
from autocan.network_profile import ECU_IDS, PLAN_BY_ID
from autocan.validation import canonical_bytes, fields, integer, json_object, number, object_value

FAULT_TYPES = (
    "ecu_offline",
    "message_drop",
    "message_delay",
    "signal_spike",
    "signal_stuck",
    "signal_bias",
    "message_frequency_anomaly",
    "cross_signal_inconsistency",
)
SIGNAL_TYPES = {"signal_spike", "signal_stuck", "signal_bias", "cross_signal_inconsistency"}
ANALOG_SIGNALS = {
    "EngineRpm",
    "Throttle",
    "CoolantTemperature",
    "FrontLeft",
    "FrontRight",
    "RearLeft",
    "RearRight",
    "BrakeObserved",
    "ThrottleObserved",
    "EngineRpmObserved",
    "Brake",
}
MAX_ACTIVE_FAULTS = 32
MAX_DELAY_NS = 1_000_000_000
MIN_PERIOD_NS = 10_000_000
MAX_PERIOD_NS = 5_000_000_000


def fault_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 64
        or not all(c.isascii() and (c.isalnum() or c in "-_:") for c in value)
    ):
        raise ValueError("fault_id must contain 1–64 ASCII letters, digits, -, _ or :")
    return value


@dataclass(frozen=True, slots=True)
class FaultSpec:
    fault_id: str
    type: str
    target_ecu: str
    target_message_id: int | None
    parameters: Mapping[str, float | int] = field(default_factory=dict)
    signal: str | None = None
    seed: int = 0

    def __post_init__(self) -> None:
        fault_id(self.fault_id)
        integer(self.seed, "fault seed")
        if self.type not in FAULT_TYPES or self.target_ecu not in ECU_IDS:
            raise ValueError("unsupported fault type or target ECU")
        if self.target_message_id is None:
            if self.type != "ecu_offline":
                raise ValueError("this fault requires a target message")
        else:
            target = integer(self.target_message_id, "target_message_id")
            plan = PLAN_BY_ID.get(target)
            if plan is None or plan.source_ecu != self.target_ecu:
                raise ValueError("fault target does not match the network DBC publisher")
        params = object_value(dict(self.parameters), "fault parameters")
        if self.type in SIGNAL_TYPES:
            assert self.target_message_id is not None
            if not isinstance(self.signal, str) or self.signal not in ANALOG_SIGNALS:
                raise ValueError("fault requires a supported analog signal")
            message = load_democar_database("democar-network").get_message_by_frame_id(
                self.target_message_id
            )
            if self.signal not in {s.name for s in message.signals}:
                raise ValueError("signal is not part of the target DBC message")
            signal = message.get_signal_by_name(self.signal)
            assert signal.minimum is not None and signal.maximum is not None
            if self.type == "cross_signal_inconsistency" and message.name != "WheelSpeeds":
                raise ValueError("cross_signal_inconsistency targets one wheel speed")
            if self.type in ("signal_spike", "signal_stuck"):
                fields(params, {"value"} if self.type == "signal_spike" else set(), {"value"})
                if "value" in params:
                    value = number(
                        params["value"],
                        "signal value",
                        minimum=signal.minimum,
                        maximum=signal.maximum,
                    )
                    raw = signal.scaled_to_raw(value)
                    decoded = signal.raw_to_scaled(raw, decode_choices=False)
                    assert isinstance(decoded, (float, int))
                    if abs(float(decoded) - value) > 1e-9:
                        raise ValueError("signal value is not exactly representable by the DBC")
            else:
                fields(
                    params, {"offset"}, {"rate_per_second"} if self.type == "signal_bias" else set()
                )
                number(params["offset"], "offset", minimum=-10000, maximum=10000)
                if "rate_per_second" in params:
                    number(
                        params["rate_per_second"], "rate_per_second", minimum=-10000, maximum=10000
                    )
        elif self.signal is not None:
            raise ValueError("this fault does not accept a signal")
        elif self.type == "message_drop":
            fields(params, {"probability"})
            number(params["probability"], "probability", minimum=0, maximum=1)
        elif self.type == "message_delay":
            fields(params, {"min_ns", "max_ns"})
            lo = integer(params["min_ns"], "min_ns")
            hi = integer(params["max_ns"], "max_ns")
            if not lo <= hi <= MAX_DELAY_NS:
                raise ValueError("delay must satisfy 0 <= min_ns <= max_ns <= 1 second")
        elif self.type == "message_frequency_anomaly":
            fields(params, {"period_ns"})
            period = integer(params["period_ns"], "period_ns", minimum=MIN_PERIOD_NS)
            if period > MAX_PERIOD_NS or period % MIN_PERIOD_NS:
                raise ValueError("period must align to 10 ms and be <= 5 seconds")
        else:
            fields(params, set())
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "FaultSpec":
        fields(
            data,
            {"fault_id", "type", "target_ecu", "target_message_id", "parameters"},
            {"signal", "seed"},
        )
        kind, ecu, signal = data["type"], data["target_ecu"], data.get("signal")
        if (
            not isinstance(kind, str)
            or not isinstance(ecu, str)
            or (signal is not None and not isinstance(signal, str))
        ):
            raise ValueError("fault type, ECU and signal must be strings")
        params = object_value(data["parameters"], "fault parameters")
        # Individual types, finite ranges and exact integer timing are checked above.
        numeric: dict[str, float | int] = {}
        for key, value in params.items():
            if isinstance(value, bool) or not isinstance(value, (float, int)):
                raise ValueError("fault parameters must be numeric")
            numeric[key] = value
        target = data["target_message_id"]
        return cls(
            fault_id(data["fault_id"]),
            kind,
            ecu,
            None if target is None else integer(target, "target_message_id"),
            numeric,
            signal,
            integer(data.get("seed", 0), "seed"),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "fault_id": self.fault_id,
            "type": self.type,
            "target_ecu": self.target_ecu,
            "target_message_id": self.target_message_id,
            "parameters": dict(self.parameters),
            "signal": self.signal,
            "seed": self.seed,
        }

    def json(self) -> str:
        return canonical_bytes(self.as_dict()).decode()

    @classmethod
    def load(cls, path: Path) -> "FaultSpec":
        with path.open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError("fault specification exceeds 4096 bytes")
        return cls.from_dict(json_object(raw.decode("utf-8")))


@dataclass(slots=True)
class ActiveFault:
    spec: FaultSpec
    start_at_ns: int
    rng: Random
    held_value: float | int | None = None


class FaultLibrary:
    def __init__(self, seed: int) -> None:
        self._seed = seed
        self.active: dict[str, ActiveFault] = {}
        self._database = load_democar_database("democar-network")

    def activate(self, spec: FaultSpec, now: int) -> bool:
        if spec.fault_id in self.active or len(self.active) >= MAX_ACTIVE_FAULTS:
            return False
        digest = sha256(f"autocan-fault-v1:{self._seed}:{spec.seed}:{spec.fault_id}".encode())
        self.active[spec.fault_id] = ActiveFault(spec, now, Random(int(digest.hexdigest(), 16)))
        return True

    def clear(self, identifier: str) -> ActiveFault | None:
        return self.active.pop(identifier, None)

    def matching(self, ecu: str, message: int) -> list[ActiveFault]:
        return [
            a
            for a in self.active.values()
            if a.spec.target_ecu == ecu and a.spec.target_message_id in (None, message)
        ]

    def offline(self, ecu: str, message: int) -> bool:
        return any(a.spec.type == "ecu_offline" for a in self.matching(ecu, message))

    def period(self, ecu: str, message: int, default: int) -> int:
        for active in self.matching(ecu, message):
            if active.spec.type == "message_frequency_anomaly":
                default = int(active.spec.parameters["period_ns"])
        return default

    def transform(self, frame: CanFrame) -> CanFrame:
        faults = [
            a for a in self.matching(frame.source_ecu, frame.can_id) if a.spec.type in SIGNAL_TYPES
        ]
        if not faults:
            return frame
        message = self._database.get_message_by_frame_id(frame.can_id)
        values = message.decode_simple(frame.payload, decode_choices=False)
        for active in faults:
            spec = active.spec
            assert spec.signal is not None
            current = values[spec.signal]
            assert isinstance(current, (float, int))
            if spec.type == "signal_stuck":
                if active.held_value is None:
                    active.held_value = spec.parameters.get("value", current)
                value = active.held_value
            elif spec.type == "signal_spike":
                value = spec.parameters["value"]
            else:
                value = (
                    current
                    + spec.parameters["offset"]
                    + spec.parameters.get("rate_per_second", 0)
                    * (frame.created_at_ns - active.start_at_ns)
                    / 1_000_000_000
                )
            signal = message.get_signal_by_name(spec.signal)
            assert signal.minimum is not None and signal.maximum is not None
            if not signal.minimum <= value <= signal.maximum:
                raise ValueError(f"fault {spec.fault_id}: {spec.signal}={value} outside DBC range")
            values[spec.signal] = value
        payload = message.encode(values, strict=True)
        # The encoder's normal nearest-step quantization applies to ramps; never clip.
        return CanFrame(
            frame.frame_id,
            frame.source_ecu,
            frame.channel,
            frame.can_id,
            frame.dlc,
            payload,
            frame.created_at_ns,
        )

    def delivery(self, ecu: str, message: int) -> tuple[bool, int]:
        dropped, delay = False, 0
        for active in self.matching(ecu, message):
            spec = active.spec
            if spec.type == "message_drop":
                # Always draw: another fault must not advance this fault's stream.
                dropped = (active.rng.random() < spec.parameters["probability"]) or dropped
            elif spec.type == "message_delay":
                delay += active.rng.randint(
                    int(spec.parameters["min_ns"]), int(spec.parameters["max_ns"])
                )
        return dropped, delay
