import pytest

from autocan.bus import VirtualCanAdapter
from autocan.events import CanFrame


def test_routed_delivery_does_not_enqueue_unsubscribed_frames() -> None:
    first = CanFrame(1, "engine", "democar", 0x200, 2, b"\x80\x0c", 0)
    second = CanFrame(2, "body", "democar", 0x230, 1, b"\x02", 10)
    with VirtualCanAdapter(receiver_ids=("A", "B"), queue_size=1) as bus:
        bus.publish_to(first, ("A",))
        bus.publish_to(second, ("B",))
        a = bus.receive("A", received_at_ns=20)
        b = bus.receive("B", received_at_ns=30)
        assert a is not None and b is not None
        assert (a.event.frame_id, b.event.frame_id) == (1, 2)
        assert a.observation.payload == first.payload
        assert b.observation.payload == second.payload
        assert bus.receive("A", received_at_ns=40) is None
        assert bus.receive("B", received_at_ns=40) is None


def test_invalid_route_is_rejected_before_partial_delivery() -> None:
    with VirtualCanAdapter(receiver_ids=("A",)) as bus:
        frame = CanFrame(1, "engine", "democar", 0x200, 2, b"\x80\x0c", 0)
        with pytest.raises(ValueError):
            bus.publish_to(frame, ("A", "unknown"))
        assert bus.receive("A", received_at_ns=0) is None
