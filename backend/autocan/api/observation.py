"""Bounded projections of delivered CAN data and explicitly labelled model telemetry."""

from collections import deque
from typing import Any

from cantools.database.can import Database

from autocan.dbc import load_democar_database
from autocan.events import Scalar
from autocan.network_ecu import Reading, kombi_display
from autocan.network_profile import ECU_IDS, GEARS, IGNITIONS, MESSAGE_PLANS, PLAN_BY_ID, RECEIVERS
from autocan.validation import integer

FRAME_LIMIT = 200
SIGNAL_LIMIT = 400


class ObservationView:
    """Decode only delivered payloads; never read VehicleModel to draw a signal."""

    def __init__(self, database: Database | None = None) -> None:
        self.frames: deque[dict[str, Scalar]] = deque(maxlen=FRAME_LIMIT)
        self.signals: deque[dict[str, Scalar]] = deque(maxlen=SIGNAL_LIMIT)
        self.pending: dict[int, dict[str, Scalar]] = {}
        self.database = database if database is not None else load_democar_database()
        self.message = self.database.get_message_by_name("EngineStatus")
        self._pending_limit = 257
        self.state: dict[str, Scalar] = {
            "sim_time_ns": 0,
            "kombi_rpm": None,
            "kombi_status": "UNAVAILABLE",
            "last_received_ns": None,
            "throttle_percent": 0,
            "brake_percent": 0,
            "gear": "P",
            "shift_rejection": None,
            "physical_speed_kph": 0,
            "engine_running": True,
            "ignition_state": "ON",
            "engine_publishing": True,
            "frames_published": 0,
            "deliveries": 0,
            "paused": True,
        }

    def consume(self, event: dict[str, Any]) -> None:
        kind = event["event_kind"]
        now = integer(event["sim_time_ns"], "event time")
        self.state["sim_time_ns"] = now
        if kind == "FramePublished":
            frame = {
                key: event[key]
                for key in ("frame_id", "event_seq", "sim_time_ns", "can_id", "dlc", "payload_hex")
            }
            self.pending[int(event["frame_id"])] = frame
            self.state["frames_published"] = event["frame_id"]
        elif kind == "FrameDropped":
            self.pending.pop(int(event["frame_id"]), None)
        elif kind == "FrameReceived":
            frame = self.pending.pop(int(event["frame_id"]))
            decoded = self.message.decode_simple(
                bytes.fromhex(str(frame["payload_hex"])), decode_choices=False
            )["EngineRpm"]
            if not isinstance(decoded, (int, float)):
                raise ValueError("invalid decoded RPM")
            rpm = float(decoded)
            self.frames.append({**frame, "sim_time_ns": now, "rpm": rpm})
            self.state.update(kombi_rpm=rpm, last_received_ns=now)
            self.state["deliveries"] = int(self.state["deliveries"] or 0) + 1
        elif kind == "EcuStatusChanged":
            self.state["kombi_status"] = event["status"]
        elif kind == "PhysicalMotionUpdated":
            self.state["physical_speed_kph"] = event["physical_speed_kph"]
        elif kind == "InputApplied":
            if event["command_kind"] == "set_throttle":
                self.state["throttle_percent"] = event["percent"]
            elif event["command_kind"] == "set_brake":
                self.state["brake_percent"] = event["percent"]
            elif event["command_kind"] == "set_gear":
                if event["accepted"]:
                    self.state["gear"] = event["gear"]
                self.state["shift_rejection"] = event["reason"]
            elif event["command_kind"] == "set_engine_publishing":
                self.state["engine_publishing"] = event["enabled"]
            elif event["command_kind"] == "set_engine_running":
                self.state["engine_running"] = event["enabled"]
                if event["enabled"]:
                    self.state["ignition_state"] = "ON"
            elif event["command_kind"] == "set_ignition":
                ignition = event["state"]
                self.state["ignition_state"] = "ON" if ignition == "START" else ignition
                if ignition in {"OFF", "ACC"}:
                    self.state["engine_running"] = False
                elif ignition == "START":
                    self.state["engine_running"] = True
        # Core already bounds pending delivery references to 256 (+1 provisional).
        if len(self.pending) > self._pending_limit:
            raise ValueError("observation delivery buffer exceeded")

    def sample(self, now: int) -> None:
        self.state["sim_time_ns"] = now
        point = {
            "sim_time_ns": now,
            "rpm": self.state["kombi_rpm"],
            "status": self.state["kombi_status"],
        }
        if self.signals and self.signals[-1]["sim_time_ns"] == now:
            self.signals[-1] = point
        else:
            self.signals.append(point)


class NetworkObservationView(ObservationView):
    def __init__(self, database: Database | None = None) -> None:
        super().__init__(
            database if database is not None else load_democar_database("democar-network")
        )
        self._pending_limit = 257 * len(RECEIVERS)
        self._remaining: dict[int, set[str]] = {}
        self._readings = {p.name: Reading({}) for p in MESSAGE_PLANS if "KOMBI" in p.receivers}
        self._display_seq = 0
        self.state.update(kombi_display(self._readings.__getitem__))
        self.state.update(
            {
                "profile_id": "democar-network",
                "driver_gear_request": "P",
                "gear_request_sequence": 0,
                "kombi_heartbeat_status": "UNAVAILABLE",
                **{f"{name.lower()}_publishing": True for name in ECU_IDS},
                **{f"{name.lower()}_frames_published": 0 for name in ECU_IDS},
            }
        )

    def _decoded(self, frame: dict[str, Scalar]) -> tuple[str, dict[str, float], str]:
        message = self.database.get_message_by_frame_id(integer(frame["can_id"], "CAN ID"))
        values = message.decode_simple(
            bytes.fromhex(str(frame["payload_hex"])),
            decode_choices=False,
            allow_truncated=False,
            allow_excess=False,
        )
        numeric: dict[str, float] = {}
        parts: list[str] = []
        for signal in message.signals:
            value = values[signal.name]
            if (
                not isinstance(value, (int, float))
                or (signal.minimum is not None and value < signal.minimum)
                or (signal.maximum is not None and value > signal.maximum)
            ):
                raise ValueError("invalid decoded network signal")
            numeric[signal.name] = float(value)
            text = f"{value:g}"
            if signal.name in ("Gear", "RequestedGear"):
                text = GEARS[int(value)]
            elif signal.name == "Ignition":
                text = IGNITIONS[int(value)]
            elif signal.name.endswith("Link") or signal.name == "InputsStatus":
                text = ("UNAVAILABLE", "FRESH", "STALE", "TIMEOUT")[int(value)]
            parts.append(f"{signal.name}={text}{' ' + signal.unit if signal.unit else ''}")
        return message.name, numeric, ", ".join(parts)

    def consume(self, event: dict[str, Any]) -> None:
        kind, now = event["event_kind"], integer(event["sim_time_ns"], "event time")
        self.state["sim_time_ns"] = now
        if kind == "FramePublished":
            frame = {
                key: event[key]
                for key in ("frame_id", "event_seq", "sim_time_ns", "can_id", "dlc", "payload_hex")
            }
            frame_id = int(event["frame_id"])
            self.pending[frame_id] = frame
            plan = PLAN_BY_ID[int(event["can_id"])]
            self._remaining[frame_id] = set(plan.receivers)
            self.state["frames_published"] = frame_id
            key = f"{plan.source_ecu.lower()}_frames_published"
            self.state[key] = int(self.state[key] or 0) + 1
        elif kind in ("FrameReceived", "FrameDropped"):
            frame_id, receiver = int(event["frame_id"]), event["receiver_id"]
            frame = self.pending[frame_id]
            if kind == "FrameReceived":
                self.state["deliveries"] = int(self.state["deliveries"] or 0) + 1
                name, values, summary = self._decoded(frame)
                if receiver == "KOMBI":
                    current = self._readings[name]
                    self._readings[name] = Reading(values, now, current.status)
                    self.state.update(kombi_display(self._readings.__getitem__))
                elif receiver == "observer":
                    self._display_seq += 1
                    self.frames.append(
                        {
                            **frame,
                            "sim_time_ns": now,
                            "message_name": name,
                            "decoded": summary,
                            "rpm": values.get("EngineRpm"),
                            "display_seq": self._display_seq,
                            "receiver_id": receiver,
                        }
                    )
            self._remaining[frame_id].remove(receiver)
            if not self._remaining[frame_id]:
                self._remaining.pop(frame_id)
                self.pending.pop(frame_id)
        elif kind == "EcuStatusChanged":
            name = event["message_name"]
            if event["ecu_id"] == "KOMBI":
                current = self._readings[name]
                self._readings[name] = Reading(
                    current.values, current.received_at_ns, event["status"]
                )
                self.state.update(kombi_display(self._readings.__getitem__))
            elif event["ecu_id"] == "observer" and name == "ClusterHeartbeat":
                self.state["kombi_heartbeat_status"] = event["status"]
        elif kind == "GearShiftEvaluated":
            self.state.update(gear=event["gear"], shift_rejection=event["reason"])
        elif kind == "InputApplied" and event["command_kind"] == "set_gear":
            self.state.update(
                driver_gear_request=event["gear"], gear_request_sequence=event["request_sequence"]
            )
        elif kind == "InputApplied" and event["command_kind"] == "set_ecu_publishing":
            self.state[f"{str(event['ecu_id']).lower()}_publishing"] = event["enabled"]
        else:
            super().consume(event)
        if len(self.pending) > self._pending_limit:
            raise ValueError("network observation buffer exceeded")


def make_view(profile_id: str, database: Database | None = None) -> ObservationView:
    view = (
        NetworkObservationView(database)
        if profile_id == "democar-network"
        else ObservationView(database)
    )
    view.state["profile_id"] = profile_id
    return view
