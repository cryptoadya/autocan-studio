"""Single-process composition of narrow ECU ports and routed virtual deliveries."""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from random import Random

from autocan.bus import BusAdapter, RoutedBusAdapter, VirtualCanAdapter
from autocan.clock import Phase, SimulationClock
from autocan.events import Scalar
from autocan.network_ecu import (
    AbsNode,
    BodyNode,
    EngineNode,
    NetworkKombi,
    NetworkReceiver,
    TransmissionNode,
)
from autocan.network_profile import ECU_IDS, MESSAGE_PLANS, RECEIVERS, MessagePlan
from autocan.vehicle import VehicleModel

type Emitter = Callable[..., None]


@dataclass(slots=True)
class DriverRequest:
    gear: str = "P"
    sequence: int = 0


class EnginePort:
    def __init__(self, vehicle: VehicleModel) -> None:
        self._vehicle = vehicle

    def read_rpm(self) -> float:
        return self._vehicle.rpm

    def read_throttle(self) -> float:
        return self._vehicle.throttle_percent

    def read_coolant(self) -> float:
        return self._vehicle.coolant_c

    def read_engine_running(self) -> bool:
        return self._vehicle.engine_running


class WheelPort:
    def __init__(self, vehicle: VehicleModel) -> None:
        self._vehicle = vehicle

    def read_wheel_speeds(self) -> tuple[float, float, float, float]:
        return self._vehicle.wheel_speeds_kph


class BodyPort:
    def __init__(self, vehicle: VehicleModel, request: DriverRequest) -> None:
        self._vehicle = vehicle
        self._request = request

    def read_ignition(self) -> str:
        return self._vehicle.ignition_state

    def read_throttle(self) -> float:
        return self._vehicle.throttle_percent

    def read_brake(self) -> float:
        return self._vehicle.brake_percent

    def read_gear_request(self) -> tuple[str, int]:
        return self._request.gear, self._request.sequence


class GearPort:
    def __init__(self, vehicle: VehicleModel, emit: Emitter) -> None:
        self._vehicle = vehicle
        self._emit = emit

    def select_gear(self, gear: str) -> tuple[bool, str | None]:
        before = self._vehicle.speed_kph
        accepted = self._vehicle.set_gear(gear)
        if before != self._vehicle.speed_kph:
            self._emit("PhysicalMotionUpdated", physical_speed_kph=self._vehicle.speed_kph)
        return accepted, self._vehicle.shift_rejection


class NetworkRuntime:
    def __init__(
        self,
        clock: SimulationClock,
        vehicle: VehicleModel,
        emit: Emitter,
        rng: Random,
        seed: int,
        delay_ns: int,
        drop_probability: float,
        bus_factory: Callable[[], BusAdapter] | None = None,
    ) -> None:
        bus = (
            bus_factory() if bus_factory is not None else VirtualCanAdapter(receiver_ids=RECEIVERS)
        )
        if not isinstance(bus, RoutedBusAdapter):
            bus.close()
            raise ValueError("network profile needs a routed bus adapter")
        self.bus = bus
        self.clock = clock
        self._emit = emit
        self._rng = rng
        self._seed = seed
        self._delay_ns = delay_ns
        self._drop_probability = drop_probability
        self.request = DriverRequest()
        self._frames = self._deliveries = 0
        self._published = dict.fromkeys(ECU_IDS, 0)
        self._publishing = dict.fromkeys(ECU_IDS, True)
        self._faults: dict[str, tuple[str, int]] = {}
        self.kombi = NetworkKombi(clock, self._status)
        self.transmission = TransmissionNode(
            clock, GearPort(vehicle, emit), self._status, self._shift
        )
        self.nodes: dict[str, NetworkReceiver] = {
            "engine": EngineNode(clock, EnginePort(vehicle), self._status),
            "abs": AbsNode(clock, WheelPort(vehicle), self._status),
            "transmission": self.transmission,
            "body": BodyNode(clock, BodyPort(vehicle, self.request), self._status),
            "KOMBI": self.kombi,
            "observer": NetworkReceiver("observer", clock, self._status),
        }
        for plan in MESSAGE_PLANS:
            clock.schedule(0, Phase.PUBLICATION, partial(self._publish, plan))

    def _status(self, ecu_id: str, message: str, status: str) -> None:
        self._emit("EcuStatusChanged", ecu_id=ecu_id, message_name=message, status=status)

    def _shift(
        self, sequence: int, requested: str, actual: str, accepted: bool, reason: str | None
    ) -> None:
        self._emit(
            "GearShiftEvaluated",
            request_sequence=sequence,
            requested_gear=requested,
            gear=actual,
            accepted=accepted,
            reason=reason,
        )

    def _publish(self, plan: MessagePlan) -> None:
        self.clock.schedule(
            self.clock.now_ns + plan.period_ns, Phase.PUBLICATION, lambda: self._publish(plan)
        )
        if not self._publishing[plan.source_ecu]:
            return
        frame = self.nodes[plan.source_ecu].publish(
            plan.name, frame_id=self._frames + 1, created_at_ns=self.clock.now_ns
        )
        dropped = tuple(
            name
            for name in plan.receivers
            if self._drop_probability > 0 and self._rng.random() < self._drop_probability
        )
        recipients = tuple(name for name in plan.receivers if name not in dropped)
        self.bus.publish_to(frame, recipients)
        self._frames += 1
        self._published[plan.source_ecu] += 1
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
        for receiver in plan.receivers:
            if receiver in dropped:
                self._emit(
                    "FrameDropped", frame_id=frame.frame_id, receiver_id=receiver, seed=self._seed
                )
            else:
                self.clock.schedule(
                    self.clock.now_ns + self._delay_ns,
                    Phase.DELIVERY,
                    partial(self._deliver, receiver),
                )

    def _deliver(self, receiver: str) -> None:
        delivery = self.bus.receive(receiver, received_at_ns=self.clock.now_ns)
        if delivery is None:
            raise RuntimeError("scheduled network delivery is missing")
        self._deliveries += 1
        self._emit(
            "FrameReceived",
            frame_id=delivery.event.frame_id,
            receiver_id=receiver,
            received_at_ns=self.clock.now_ns,
        )
        self.nodes[receiver].receive(delivery.observation)

    def request_gear(self, gear: str) -> int:
        self.request.gear = gear
        self.request.sequence = (self.request.sequence + 1) % 65536
        return self.request.sequence

    def set_publishing(self, ecu_id: str, enabled: bool, command_seq: int) -> None:
        if self._publishing[ecu_id] == enabled:
            return
        self._publishing[ecu_id] = enabled
        if enabled:
            fault_id, start = self._faults.pop(ecu_id)
            self._emit(
                "FaultCleared",
                fault_id=fault_id,
                type="ecu_offline",
                target_ecu=ecu_id,
                target_message_id=None,
                start_at_ns=start,
                end_at_ns=self.clock.now_ns,
                seed=self._seed,
            )
        else:
            fault_id = f"{ecu_id}-offline-{command_seq}"
            self._faults[ecu_id] = (fault_id, self.clock.now_ns)
            self._emit(
                "FaultActivated",
                fault_id=fault_id,
                type="ecu_offline",
                target_ecu=ecu_id,
                target_message_id=None,
                start_at_ns=self.clock.now_ns,
                seed=self._seed,
            )

    def state(self) -> dict[str, Scalar]:
        return {
            **self.kombi.display_state(),
            "kombi_heartbeat_status": self.nodes["observer"].status("ClusterHeartbeat"),
            "frames_published": self._frames,
            "deliveries": self._deliveries,
            "shift_rejection": self.transmission.reason,
            "driver_gear_request": self.request.gear,
            "gear_request_sequence": self.request.sequence,
            **{f"{name.lower()}_publishing": enabled for name, enabled in self._publishing.items()},
            **{
                f"{name.lower()}_frames_published": count for name, count in self._published.items()
            },
        }

    @staticmethod
    def configuration() -> dict[str, object]:
        return {
            "messages": [
                {
                    "name": p.name,
                    "source_ecu": p.source_ecu,
                    "can_id": p.can_id,
                    "period_ns": p.period_ns,
                    "receivers": list(p.receivers),
                }
                for p in MESSAGE_PLANS
            ],
            "stale_periods": 3,
            "timeout_periods": 10,
            "coolant_initial_c": 20,
            "coolant_target_c": 90,
            "coolant_warm_c_per_second": 0.2,
            "coolant_cool_c_per_second": 0.05,
        }
