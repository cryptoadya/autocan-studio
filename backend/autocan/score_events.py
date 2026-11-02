"""Temporal event metrics from fixed saved decisions, without CAN attribution."""

from dataclasses import dataclass, replace

from autocan.evaluation import (
    MAX_ALERT_EPISODES,
    AlertEpisode,
    EpisodeMatch,
    FaultEpisode,
    RuleResult,
)
from autocan.validation import integer
from autocan.window_evaluation import WindowKey

EVENT_EVALUATOR_VERSION = "saved-score-events-1"
MATCHING_POLICY = (
    "temporal-only, onset-ordered one-to-one; earliest end+window, start, fault ID, "
    "saved order; onset in [fault start, clipped end+window); no preexisting credit"
)


@dataclass(frozen=True, slots=True)
class RunEvents:
    result: RuleResult
    unscored_faults: tuple[FaultEpisode, ...]
    left_censored_fault_ids: tuple[str, ...]
    right_censored_fault_ids: tuple[str, ...]
    evaluated_start_ns: int | None
    evaluated_end_ns: int | None
    eligible_windows: int


def evaluate_score_events(
    keys: list[WindowKey],
    decisions: list[bool],
    faults: list[FaultEpisode],
    *,
    model_id: str,
    window_ns: int,
) -> RunEvents:
    """Group eligible positives and match their *decision-time* onsets to truth.

    All detectors are global anomaly detectors here. Target CAN IDs are retained
    in truth for audit, but never invented for alarms or used to claim localisation.
    Left-censored faults retain their original start when calculating latency.
    """
    integer(window_ns, "event window", minimum=1)
    if not model_id.strip():
        raise ValueError("event model ID must be nonempty")
    selected: list[tuple[WindowKey, bool]] = []
    eligible_seen = False
    for i, (key, positive) in enumerate(zip(keys, decisions, strict=True)):
        if (
            type(positive) is not bool
            or key.start_ns != i * window_ns
            or key.end_ns != (i + 1) * window_ns
            or key.run_id != keys[0].run_id
            or key.split != keys[0].split
            or key.row_index != keys[0].row_index + i
            or (eligible_seen and not key.eligible)
        ):
            raise ValueError("event window alignment/identity/eligibility mismatch")
        eligible_seen |= key.eligible
        if key.eligible:
            selected.append((key, positive))
    if len({f.fault_id for f in faults}) != len(faults):
        raise ValueError("duplicate fault identity")
    for fault in faults:
        integer(fault.start_ns, "fault start")
        integer(fault.end_ns, "fault end", minimum=fault.start_ns)
    if not selected:
        return RunEvents(RuleResult((), (), (), 0), tuple(faults), (), (), None, None, 0)
    start, end = selected[0][0].start_ns, selected[-1][0].end_ns
    alarms: list[AlertEpisode] = []
    active: int | None = None

    def append_alarm(at: int | None) -> None:
        assert active is not None
        if len(alarms) >= MAX_ALERT_EPISODES:
            raise ValueError("event alarm episode quota exceeded")
        alarms.append(AlertEpisode(model_id, active, at, ()))

    for key, positive in selected:
        if positive and active is None:
            active = key.end_ns
        elif not positive and active is not None:
            append_alarm(key.end_ns)
            active = None
    if active is not None:
        append_alarm(None)
    scored, unscored, left, right = [], [], [], []
    for fault in faults:
        if fault.end_ns <= fault.start_ns or fault.end_ns <= start or fault.start_ns > end:
            unscored.append(fault)
            continue
        clipped = replace(
            fault, end_ns=min(end, fault.end_ns), censored=fault.censored or fault.end_ns > end
        )
        scored.append(clipped)
        if fault.start_ns < start:
            left.append(fault.fault_id)
        if clipped.censored:
            right.append(fault.fault_id)
    matches: list[EpisodeMatch] = []
    used: set[int] = set()
    for alert_index, alarm in enumerate(alarms):
        candidates = [
            (fault.end_ns + window_ns, fault.start_ns, fault.fault_id, i)
            for i, fault in enumerate(scored)
            if i not in used and fault.start_ns <= alarm.start_ns < fault.end_ns + window_ns
        ]
        if candidates:
            index = min(candidates)[-1]
            used.add(index)
            matches.append(
                EpisodeMatch(index, alert_index, alarm.start_ns - scored[index].start_ns)
            )
    # Fault runs never contribute quiet segments to the normal exposure denominator.
    exposure = sum(k.end_ns - k.start_ns for k, _ in selected) if not faults else 0
    return RunEvents(
        RuleResult(tuple(scored), tuple(alarms), tuple(matches), exposure),
        tuple(unscored),
        tuple(left),
        tuple(right),
        start,
        end,
        len(selected),
    )
