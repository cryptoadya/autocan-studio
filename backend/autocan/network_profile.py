"""The versioned DBC owns network message IDs, periods and receiver routes."""

from dataclasses import dataclass

from autocan.dbc import load_democar_database

ECU_IDS = ("engine", "abs", "transmission", "body", "KOMBI")
RECEIVERS = (*ECU_IDS, "observer")
NODE_IDS = {
    "Engine": "engine",
    "ABS": "abs",
    "Transmission": "transmission",
    "Body": "body",
    "KOMBI": "KOMBI",
    "Observer": "observer",
}
GEARS = ("P", "R", "N", "D")
IGNITIONS = ("OFF", "ACC", "ON", "START")
STATUS_CODES = {"UNAVAILABLE": 0, "FRESH": 1, "STALE": 2, "TIMEOUT": 3}
SHIFT_REASONS = {None: 0, "vehicle_moving": 1, "brake_required": 2, "inputs_unavailable": 3}


@dataclass(frozen=True, slots=True)
class MessagePlan:
    name: str
    source_ecu: str
    can_id: int
    period_ns: int
    receivers: tuple[str, ...]
    dlc: int


def _plans() -> tuple[MessagePlan, ...]:
    result = []
    for message in load_democar_database("democar-network").messages:
        if message.cycle_time is None or message.cycle_time <= 0 or len(message.senders) != 1:
            raise ValueError("network messages need one publisher and a positive period")
        subscribed = {NODE_IDS[name] for signal in message.signals for name in signal.receivers}
        result.append(
            MessagePlan(
                message.name,
                NODE_IDS[message.senders[0]],
                message.frame_id,
                message.cycle_time * 1_000_000,
                tuple(name for name in RECEIVERS if name in subscribed),
                message.length,
            )
        )
    return tuple(result)


MESSAGE_PLANS = _plans()
PLAN_BY_NAME = {plan.name: plan for plan in MESSAGE_PLANS}
PLAN_BY_ID = {plan.can_id: plan for plan in MESSAGE_PLANS}
