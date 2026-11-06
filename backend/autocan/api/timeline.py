"""Bounded demo ground truth, kept apart from decoded CAN measurements."""

from collections import deque
from typing import Any, TypedDict

from autocan.traces import ScoreSample
from autocan.validation import json_object, number

TIMELINE_LIMIT = 100
SCORE_VIEW_LIMIT = 240


class AlertEpisode(TypedDict):
    start_ns: int
    end_ns: int | None
    peak_score: float


class AnomalyTimeline:
    def __init__(self, model: dict[str, object]) -> None:
        self.model = model
        self.threshold = number(model["threshold"], "model threshold", minimum=0, maximum=1)
        self.scores: deque[ScoreSample] = deque(maxlen=SCORE_VIEW_LIMIT)
        self.alerts: deque[AlertEpisode] = deque(maxlen=TIMELINE_LIMIT)
        self._active: AlertEpisode | None = None
        self._below = 0

    def consume(self, sample: ScoreSample) -> None:
        self.scores.append(sample)
        if not sample["eligible"]:
            return
        score = sample["score"]
        if score >= self.threshold:
            self._below = 0
            if self._active is None:
                self._active = {
                    "start_ns": sample["sim_time_ns"],
                    "end_ns": None,
                    "peak_score": score,
                }
                self.alerts.append(self._active)
            else:
                self._active["peak_score"] = max(self._active["peak_score"], score)
        elif self._active is not None:
            self._below += 1
            if self._below == 2:
                self._active["end_ns"] = sample["sim_time_ns"]
                self._active = None

    def snapshot(self) -> dict[str, object]:
        return {"model": self.model, "scores": list(self.scores), "alerts": list(self.alerts)}


class FaultTimeline:
    def __init__(self) -> None:
        self.events: deque[dict[str, Any]] = deque(maxlen=TIMELINE_LIMIT)
        self.active: dict[str, dict[str, Any]] = {}
        self.total = 0

    def consume(self, event: dict[str, Any]) -> None:
        kind = event["event_kind"]
        row: dict[str, Any] = {
            key: event[key] for key in ("event_seq", "sim_time_ns", "event_kind")
        }
        if kind in {"FaultActivated", "FaultCleared"}:
            row.update(
                {
                    key: event[key]
                    for key in (
                        "fault_id",
                        "type",
                        "target_ecu",
                        "target_message_id",
                        "start_at_ns",
                    )
                }
            )
            row["title"] = "Fault activated" if kind == "FaultActivated" else "Fault cleared"
            row["details"] = (
                f"{event['fault_id']} · {event['type'].replace('_', ' ')} · {event['target_ecu']}"
            )
            if "fault_json" in event:
                if kind == "FaultActivated":
                    self.active[event["fault_id"]] = {
                        "fault": json_object(event["fault_json"]),
                        "start_at_ns": event["start_at_ns"],
                    }
                else:
                    self.active.pop(event["fault_id"], None)
        elif kind == "InputApplied":
            command = event["command_kind"]
            if command in {"inject_fault", "clear_fault"}:
                if event["accepted"]:
                    return  # The accompanying marker carries the episode boundary.
                row["title"] = "Command rejected"
                identifier = event.get("fault_id") or json_object(event["fault_json"])["fault_id"]
                row["details"] = f"{command} · {identifier} · no state change"
            else:
                row["title"] = command.replace("set_", "", 1).replace("_", " ").capitalize()
                row["details"] = " · ".join(
                    f"{key}={event[key]}"
                    for key in ("percent", "gear", "state", "enabled", "ecu_id")
                    if key in event
                )
        elif kind == "EcuStatusChanged" and event["ecu_id"] == "KOMBI":
            row["title"] = f"KOMBI {event['status']}"
            row["details"] = event.get("message_name", "EngineStatus")
        else:
            return
        self.total += 1
        self.events.append(row)

    def snapshot(self) -> dict[str, Any]:
        return {
            "timeline": list(self.events),
            "timeline_truncated": self.total - len(self.events),
            "active_faults": list(self.active.values()),
        }
