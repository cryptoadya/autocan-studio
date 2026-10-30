"""Headless composition; no recorder, fault injector, API, or wall-clock pacing."""

from collections.abc import Callable
from types import TracebackType
from typing import Self
from uuid import uuid4

from autocan.bus import BusAdapter, VirtualCanAdapter
from autocan.clock import Phase, SimulationClock, validate_time
from autocan.ecu import EngineEcu, Kombi
from autocan.events import RunEvent, Scalar
from autocan.vehicle import VehicleModel, validate_throttle

PHYSICS_TICK_NS = 10_000_000
PUBLICATION_PERIOD_NS = 20_000_000


class _VehicleRpmSensor:
    def __init__(self, vehicle: VehicleModel) -> None:
        self._vehicle = vehicle

    def read_rpm(self) -> float:
        return self._vehicle.rpm


class Simulation:
    def __init__(
        self,
        *,
        seed: int = 0,
        on_event: Callable[[RunEvent], None] | None = None,
        bus_factory: Callable[[], BusAdapter] = VirtualCanAdapter,
    ) -> None:
        if type(seed) is not int or seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        self._seed = seed
        self._on_event = on_event
        self._bus_factory = bus_factory
        self._closed = False
        self._initialize()

    def _initialize(self) -> None:
        self._run_id = uuid4().hex
        self._clock = SimulationClock()
        self._vehicle = VehicleModel()
        self._engine = EngineEcu(_VehicleRpmSensor(self._vehicle))
        self._kombi = Kombi()
        self._bus = self._bus_factory()
        self._frame_seq = self._event_seq = self._command_seq = self._deliveries = 0
        self._clock.schedule(0, Phase.PUBLICATION, self._publish)
        self._clock.schedule(PHYSICS_TICK_NS, Phase.PHYSICS, self._physics_tick)

    def _emit(self, kind: str, **data: Scalar) -> None:
        self._event_seq += 1
        if self._on_event is not None:
            self._on_event(RunEvent(self._run_id, self._event_seq, self._clock.now_ns, kind, data))

    def _physics_tick(self) -> None:
        self._vehicle.tick(PHYSICS_TICK_NS)
        self._clock.schedule(
            self._clock.now_ns + PHYSICS_TICK_NS, Phase.PHYSICS, self._physics_tick
        )

    def _publish(self) -> None:
        frame = self._engine.publish(frame_id=self._frame_seq + 1, created_at_ns=self._clock.now_ns)
        self._bus.publish(frame)
        self._frame_seq += 1
        self._emit(
            "FramePublished",
            frame_id=frame.frame_id,
            source_ecu=frame.source_ecu,
            channel=frame.channel,
            can_id=frame.can_id,
            dlc=frame.dlc,
            is_extended_id=frame.is_extended_id,
            payload_hex=frame.payload.hex(),
            created_at_ns=frame.created_at_ns,
        )
        self._clock.schedule(self._clock.now_ns, Phase.DELIVERY, self._deliver)
        self._clock.schedule(
            self._clock.now_ns + PUBLICATION_PERIOD_NS, Phase.PUBLICATION, self._publish
        )

    def _deliver(self) -> None:
        delivery = self._bus.receive("KOMBI", received_at_ns=self._clock.now_ns)
        if delivery is None:
            raise RuntimeError("scheduled CAN delivery is missing")
        self._deliveries += 1
        event = delivery.event
        self._emit(
            "FrameReceived",
            frame_id=event.frame_id,
            receiver_id=event.receiver_id,
            received_at_ns=event.received_at_ns,
        )
        previous = self._kombi.state.status
        self._kombi.receive(delivery.observation)
        if previous != self._kombi.state.status:
            self._emit("EcuStatusChanged", ecu_id="KOMBI", status=self._kombi.state.status)

    def set_throttle(self, percent: float, *, at_ns: int | None = None) -> int:
        self._check_open()
        validate_throttle(percent)
        if at_ns is None:
            at_ns = (
                (self._clock.now_ns + PHYSICS_TICK_NS - 1) // PHYSICS_TICK_NS
            ) * PHYSICS_TICK_NS
            if not self._clock.can_schedule(at_ns, Phase.INPUT):
                at_ns += PHYSICS_TICK_NS
        validate_time(at_ns)
        if at_ns % PHYSICS_TICK_NS:
            raise ValueError("commands must align with a physics tick")

        def apply() -> None:
            self._vehicle.set_throttle(percent)
            self._command_seq += 1
            self._emit(
                "InputApplied",
                command_seq=self._command_seq,
                applied_at_ns=self._clock.now_ns,
                command_kind="set_throttle",
                percent=percent,
            )

        self._clock.schedule(at_ns, Phase.INPUT, apply)
        return at_ns

    def advance_to(self, target_ns: int) -> None:
        self._check_open()
        self._clock.advance_to(target_ns)

    def step(self) -> None:
        self._check_open()
        self._clock.step_to(self._clock.now_ns + PHYSICS_TICK_NS)

    def pause(self) -> None:
        self._check_open()
        self._clock.pause()

    def resume(self) -> None:
        self._check_open()
        self._clock.resume()

    def state(self) -> dict[str, Scalar]:
        return {
            "sim_time_ns": self._clock.now_ns,
            "paused": self._clock.paused,
            "seed": self._seed,
            "throttle_percent": self._vehicle.throttle_percent,
            "physical_rpm": self._vehicle.rpm,
            "kombi_rpm": self._kombi.state.rpm,
            "kombi_status": self._kombi.state.status,
            "last_received_ns": self._kombi.state.last_received_ns,
            "frames_published": self._frame_seq,
            "deliveries": self._deliveries,
        }

    def reset(self) -> None:
        self._check_open()
        self._clock.reset()
        self._bus.close()
        self._initialize()

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("simulation is closed")

    def close(self) -> None:
        if not self._closed:
            self._bus.close()
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
