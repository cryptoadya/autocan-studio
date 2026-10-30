"""Wire messages are immutable; receiver observations omit simulator provenance."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from autocan.clock import validate_time

type Scalar = str | int | float | bool | None


def _validate_wire(can_id: int, extended: bool, dlc: int, payload: bytes) -> None:
    if type(can_id) is not int or not 0 <= can_id <= 0x7FF or extended is not False:
        raise ValueError("only standard 11-bit Classic CAN frames are supported")
    if type(dlc) is not int or not 0 <= dlc <= 8 or not isinstance(payload, bytes):
        raise ValueError("DLC must be in [0, 8] and payload must be immutable bytes")
    if len(payload) != dlc:
        raise ValueError("payload length must equal DLC")


@dataclass(frozen=True, slots=True)
class CanFrame:
    frame_id: int
    source_ecu: str
    channel: str
    can_id: int
    dlc: int
    payload: bytes
    created_at_ns: int
    is_extended_id: bool = False

    def __post_init__(self) -> None:
        _validate_wire(self.can_id, self.is_extended_id, self.dlc, self.payload)
        validate_time(self.created_at_ns)
        if type(self.frame_id) is not int or self.frame_id <= 0:
            raise ValueError("frame_id must be a positive integer")
        if not self.source_ecu or not self.channel:
            raise ValueError("source_ecu and channel are required")


@dataclass(frozen=True, slots=True)
class CanObservation:
    channel: str
    can_id: int
    is_extended_id: bool
    dlc: int
    payload: bytes
    received_at_ns: int

    def __post_init__(self) -> None:
        _validate_wire(self.can_id, self.is_extended_id, self.dlc, self.payload)
        validate_time(self.received_at_ns)


@dataclass(frozen=True, slots=True)
class DeliveryEvent:
    frame_id: int
    receiver_id: str
    received_at_ns: int


@dataclass(frozen=True, slots=True)
class Delivery:
    event: DeliveryEvent
    observation: CanObservation


@dataclass(frozen=True, slots=True)
class RunEvent:
    run_id: str
    event_seq: int
    sim_time_ns: int
    event_kind: str
    data: Mapping[str, Scalar]

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "event_seq": self.event_seq,
            "sim_time_ns": self.sim_time_ns,
            "event_kind": self.event_kind,
            "schema_version": 1,
            "profile_version": "democar-0.1.0",
            **self.data,
        }
