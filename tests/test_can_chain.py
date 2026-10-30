"""Immutable wire data and actual in-process python-can delivery."""

from dataclasses import FrozenInstanceError

import pytest


def test_engine_sensor_is_encoded_and_received_by_kombi() -> None:
    from autocan.bus import VirtualCanAdapter
    from autocan.ecu import EngineEcu, Kombi

    class Sensor:
        def read_rpm(self) -> float:
            return 1234.25

    frame = EngineEcu(Sensor()).publish(frame_id=1, created_at_ns=0)
    assert frame.payload.hex() == "4913"
    with pytest.raises(FrozenInstanceError):
        frame.can_id = 0x201  # type: ignore[misc]
    kombi = Kombi()
    assert kombi.state.rpm is None
    with VirtualCanAdapter() as bus:
        bus.publish(frame)
        delivery = bus.receive("KOMBI", received_at_ns=20_000_000)
        assert delivery is not None
        assert delivery.event.frame_id == 1
        assert delivery.event.receiver_id == "KOMBI"
        assert delivery.event.received_at_ns == 20_000_000
        assert kombi.receive(delivery.observation)
        assert kombi.state.rpm == 1234.25
        assert kombi.state.last_received_ns == 20_000_000


def test_one_frame_can_have_distinct_receiver_times_without_float_roundtrip() -> None:
    from autocan.bus import VirtualCanAdapter
    from autocan.events import CanFrame

    created = 1_000_000_000_000_000_001
    frame = CanFrame(1, "engine", "democar", 0x200, 2, b"\x80\x0c", created)
    with VirtualCanAdapter(receiver_ids=("KOMBI", "monitor")) as bus:
        bus.publish(frame)
        first = bus.receive("KOMBI", received_at_ns=created + 1)
        second = bus.receive("monitor", received_at_ns=created + 2)
        assert first is not None and second is not None
        assert first.event.frame_id == second.event.frame_id == 1
        assert first.observation.received_at_ns == created + 1
        assert second.observation.received_at_ns == created + 2
        assert frame.created_at_ns == created
        assert bus.receive("KOMBI", received_at_ns=created + 3) is None


def test_bus_overflow_is_explicit_and_does_not_drop_oldest() -> None:
    from autocan.bus import VirtualCanAdapter
    from autocan.events import CanFrame

    frame = CanFrame(1, "engine", "democar", 0x200, 2, b"\x80\x0c", 0)
    with VirtualCanAdapter(queue_size=1) as bus:
        bus.publish(frame)
        with pytest.raises(RuntimeError, match="queue"):
            bus.publish(CanFrame(2, "engine", "democar", 0x200, 2, b"\xa0\x0f", 0))
        delivery = bus.receive("KOMBI", received_at_ns=0)
        assert delivery is not None
        assert delivery.observation.payload.hex() == "800c"


@pytest.mark.parametrize("payload", [b"\x80", b"\x80\x0c\x00", b"\xff\xff"])
def test_kombi_rejects_invalid_data_without_refreshing(payload: bytes) -> None:
    from autocan.ecu import Kombi
    from autocan.events import CanObservation

    kombi = Kombi()
    kombi.receive(CanObservation("democar", 0x200, False, 2, b"\x80\x0c", 0))
    with pytest.raises(ValueError):
        kombi.receive(CanObservation("democar", 0x200, False, len(payload), payload, 10))
    assert kombi.state.rpm == 800
    assert kombi.state.last_received_ns == 0


def test_virtual_bus_instances_do_not_cross_deliver() -> None:
    from autocan.bus import VirtualCanAdapter
    from autocan.events import CanFrame

    with VirtualCanAdapter() as first, VirtualCanAdapter() as second:
        first.publish(CanFrame(1, "engine", "democar", 0x200, 2, b"\x80\x0c", 0))
        assert second.receive("KOMBI", received_at_ns=0) is None


def test_frame_rejects_mutable_payload_and_invalid_classic_can() -> None:
    from autocan.events import CanFrame

    with pytest.raises(ValueError):
        CanFrame(1, "engine", "democar", 0x800, 2, b"\x00\x00", 0)
    with pytest.raises(ValueError):
        CanFrame(1, "engine", "democar", 0x200, 2, bytearray(2), 0)  # type: ignore[arg-type]
