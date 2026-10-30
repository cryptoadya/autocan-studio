"""Bounded in-process python-can transport; the core supplies all receive times."""

from collections import deque
from types import TracebackType
from typing import Protocol, Self
from uuid import uuid4

from can import CanOperationError, Message
from can.interfaces.virtual import VirtualBus

from autocan.clock import validate_time
from autocan.events import CanFrame, CanObservation, Delivery, DeliveryEvent


class BusAdapter(Protocol):
    def publish(self, frame: CanFrame) -> None: ...
    def receive(self, receiver_id: str, *, received_at_ns: int) -> Delivery | None: ...
    def close(self) -> None: ...


class VirtualCanAdapter:
    def __init__(
        self, *, receiver_ids: tuple[str, ...] = ("KOMBI",), queue_size: int = 256
    ) -> None:
        if type(queue_size) is not int or queue_size <= 0:
            raise ValueError("queue_size must be positive")
        if not receiver_ids or len(set(receiver_ids)) != len(receiver_ids) or not all(receiver_ids):
            raise ValueError("receiver IDs must be nonempty and unique")
        channel = f"autocan-{uuid4().hex}"
        self._tx = VirtualBus(channel, preserve_timestamps=True, rx_queue_size=queue_size)
        self._receivers = {
            name: VirtualBus(channel, preserve_timestamps=True, rx_queue_size=queue_size)
            for name in receiver_ids
        }
        self._pending: dict[str, deque[CanFrame]] = {name: deque() for name in receiver_ids}
        self._queue_size = queue_size
        self._closed = False

    def publish(self, frame: CanFrame) -> None:
        self._check_open()
        if frame.channel != "democar":
            raise ValueError("unknown logical CAN channel")
        if any(len(queue) >= self._queue_size for queue in self._pending.values()):
            raise RuntimeError("virtual CAN receive queue is full")
        message = Message(
            timestamp=0.0,  # canonical integer times never travel through this float
            arbitration_id=frame.can_id,
            is_extended_id=frame.is_extended_id,
            dlc=frame.dlc,
            data=frame.payload,
            check=True,
        )
        try:
            self._tx.send(message, timeout=0)
        except CanOperationError as exc:
            raise RuntimeError("virtual CAN queue delivery failed") from exc
        for queue in self._pending.values():
            queue.append(frame)

    def receive(self, receiver_id: str, *, received_at_ns: int) -> Delivery | None:
        self._check_open()
        validate_time(received_at_ns)
        if receiver_id not in self._receivers:
            raise ValueError("unknown receiver")
        queue = self._pending[receiver_id]
        if not queue:
            return None
        frame = queue[0]
        if received_at_ns < frame.created_at_ns:
            raise ValueError("delivery cannot precede frame creation")
        message = self._receivers[receiver_id].recv(timeout=0)
        if message is None:
            raise RuntimeError("virtual CAN frame missing from receiver queue")
        queue.popleft()
        observation = CanObservation(
            frame.channel,
            message.arbitration_id,
            message.is_extended_id,
            message.dlc,
            bytes(message.data),
            received_at_ns,
        )
        return Delivery(DeliveryEvent(frame.frame_id, receiver_id, received_at_ns), observation)

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("virtual CAN adapter is closed")

    def close(self) -> None:
        if not self._closed:
            self._tx.shutdown()
            for receiver in self._receivers.values():
                receiver.shutdown()
            for queue in self._pending.values():
                queue.clear()
            self._closed = True

    def __enter__(self) -> Self:
        self._check_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
