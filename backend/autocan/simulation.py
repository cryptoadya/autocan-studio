"""Headless composition; no recorder, fault injector, API, or wall-clock pacing."""

import math
from collections.abc import Callable
from hashlib import sha256
from random import Random
from types import TracebackType
from typing import Self
from uuid import uuid4

from autocan.bus import BusAdapter, VirtualCanAdapter
from autocan.clock import Phase, SimulationClock, validate_time
from autocan.ecu import EngineEcu, Kombi, KombiState
from autocan.events import RunEvent, Scalar
from autocan.vehicle import VehicleModel, validate_ignition, validate_throttle

PHYSICS_TICK_NS = 10_000_000
PUBLICATION_PERIOD_NS = 20_000_000


def rng_stream_seeds(seed: int) -> dict[str, str]:
    return {
        name: sha256(f"autocan-rng-v1:{seed}:{name}".encode()).hexdigest()
        for name in ("physics", "scenario", "delivery")
    }


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
        delivery_delay_ns: int = 0,
        drop_probability: float = 0,
    ) -> None:
        if type(seed) is not int or seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        validate_time(delivery_delay_ns)
        if (
            isinstance(drop_probability, bool)
            or not math.isfinite(drop_probability)
            or not 0 <= drop_probability <= 1
        ):
            raise ValueError("drop_probability must be finite and in [0, 1]")
        self._seed = seed
        self._delivery_delay_ns = delivery_delay_ns
        self._drop_probability = float(drop_probability)
        self._on_event = on_event
        self._bus_factory = bus_factory
        self._closed = False
        self._initialize()

    def _initialize(self) -> None:
        self._run_id = uuid4().hex
        self._clock = SimulationClock()
        self._vehicle = VehicleModel()
        self._engine = EngineEcu(_VehicleRpmSensor(self._vehicle))
        self._kombi = Kombi(clock=self._clock, on_status=self._kombi_status)
        self._bus = self._bus_factory()
        self._frame_seq = self._event_seq = self._command_seq = self._deliveries = 0
        self._engine_publishing = True
        self._offline_fault: tuple[str, int] | None = None
        self._delivery_rng = Random(int(rng_stream_seeds(self._seed)["delivery"], 16))
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
        self._clock.schedule(
            self._clock.now_ns + PUBLICATION_PERIOD_NS, Phase.PUBLICATION, self._publish
        )
        if not self._engine_publishing:
            return
        frame = self._engine.publish(frame_id=self._frame_seq + 1, created_at_ns=self._clock.now_ns)
        dropped = (
            self._drop_probability > 0 and self._delivery_rng.random() < self._drop_probability
        )
        if not dropped:
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
        if dropped:
            self._emit(
                "FrameDropped", frame_id=frame.frame_id, receiver_id="KOMBI", seed=self._seed
            )
        else:
            self._clock.schedule(
                self._clock.now_ns + self._delivery_delay_ns, Phase.DELIVERY, self._deliver
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
        self._kombi.receive(delivery.observation)

    def _kombi_status(self, state: KombiState) -> None:
        self._emit("EcuStatusChanged", ecu_id="KOMBI", status=state.status)

    def set_throttle(self, percent: float, *, at_ns: int | None = None) -> int:
        self._check_open()
        validate_throttle(percent)
        percent = float(percent)

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

        return self._queue_input(at_ns, apply)

    def set_engine_publishing(self, enabled: bool, *, at_ns: int | None = None) -> int:
        self._check_open()
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")

        def apply() -> None:
            self._command_seq += 1
            self._emit(
                "InputApplied",
                command_seq=self._command_seq,
                applied_at_ns=self._clock.now_ns,
                command_kind="set_engine_publishing",
                enabled=enabled,
            )
            if self._engine_publishing == enabled:
                return
            self._engine_publishing = enabled
            if enabled:
                assert self._offline_fault is not None
                fault_id, start = self._offline_fault
                self._emit(
                    "FaultCleared",
                    fault_id=fault_id,
                    type="ecu_offline",
                    target_ecu="engine",
                    target_message_id=0x200,
                    start_at_ns=start,
                    end_at_ns=self._clock.now_ns,
                    seed=self._seed,
                )
                self._offline_fault = None
            else:
                fault_id = f"engine-offline-{self._command_seq}"
                self._offline_fault = (fault_id, self._clock.now_ns)
                self._emit(
                    "FaultActivated",
                    fault_id=fault_id,
                    type="ecu_offline",
                    target_ecu="engine",
                    target_message_id=0x200,
                    start_at_ns=self._clock.now_ns,
                    seed=self._seed,
                )

        return self._queue_input(at_ns, apply)

    def set_engine_running(self, enabled: bool, *, at_ns: int | None = None) -> int:
        self._check_open()
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")

        def apply() -> None:
            self._vehicle.set_engine_running(enabled)
            self._command_seq += 1
            self._emit(
                "InputApplied",
                command_seq=self._command_seq,
                applied_at_ns=self._clock.now_ns,
                command_kind="set_engine_running",
                enabled=enabled,
            )

        return self._queue_input(at_ns, apply)

    def set_ignition(self, state: str, *, at_ns: int | None = None) -> int:
        self._check_open()
        validate_ignition(state)

        def apply() -> None:
            self._vehicle.set_ignition(state)
            self._command_seq += 1
            self._emit(
                "InputApplied",
                command_seq=self._command_seq,
                applied_at_ns=self._clock.now_ns,
                command_kind="set_ignition",
                state=state,
            )

        return self._queue_input(at_ns, apply)

    def _queue_input(self, at_ns: int | None, apply: Callable[[], None]) -> int:
        if at_ns is None:
            at_ns = (
                (self._clock.now_ns + PHYSICS_TICK_NS - 1) // PHYSICS_TICK_NS
            ) * PHYSICS_TICK_NS
            if not self._clock.can_schedule(at_ns, Phase.INPUT):
                at_ns += PHYSICS_TICK_NS
        validate_time(at_ns)
        if at_ns % PHYSICS_TICK_NS:
            raise ValueError("commands must align with a physics tick")

        self._clock.schedule(at_ns, Phase.INPUT, apply)
        return at_ns

    @property
    def run_id(self) -> str:
        return self._run_id

    def configuration(self) -> dict[str, object]:
        return {
            "seed": self._seed,
            "delivery_delay_ns": self._delivery_delay_ns,
            "drop_probability": self._drop_probability,
            "physics_tick_ns": PHYSICS_TICK_NS,
            "publication_period_ns": PUBLICATION_PERIOD_NS,
            "stale_after_ns": 60_000_000,
            "timeout_after_ns": 200_000_000,
            "rng_streams": rng_stream_seeds(self._seed),
            "rng_algorithm": "sha256-namespaced-v1/python.Random",
        }

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
            "events_emitted": self._event_seq,
            "engine_running": self._vehicle.engine_running,
            "ignition_state": self._vehicle.ignition_state,
            "engine_publishing": self._engine_publishing,
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
