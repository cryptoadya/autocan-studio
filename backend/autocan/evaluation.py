"""Whole-run partitions and episode scoring; ground truth stays in this evaluator."""

from collections.abc import Iterable
from dataclasses import dataclass
from math import ceil
from random import Random
from typing import TypedDict

from autocan.factory import FactoryManifest
from autocan.validation import integer, object_value

RULE_IDS = ("heartbeat_timeout", "rpm_derivative", "wheel_residual")
SPLITS = ("train", "validation", "test")
EVALUATOR_VERSION = "tier0-benchmark-1"
MAX_ALERT_EPISODES = 10_000


class Assignment(TypedDict):
    index: int
    run_id: str
    group: int
    split: str


class SplitManifest(TypedDict):
    format: str
    format_version: int
    dataset_semantic_sha256: str
    seed: int
    assignments: list[Assignment]


def make_split(meta: FactoryManifest, *, seed: int) -> SplitManifest:
    """Shuffle connected groups of identical scenarios OR observed inputs."""
    if integer(seed, "split seed") > 2**63 - 1:
        raise ValueError("split seed must fit a nonnegative signed 64-bit integer")
    runs = sorted(meta["runs"], key=lambda row: row["index"])
    if len({row["run_id"] for row in runs}) != len(runs) or len(
        {row["index"] for row in runs}
    ) != len(runs):
        raise ValueError("split requires unique run identities and indices")
    parent = list(range(len(runs)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen: dict[tuple[str, str], int] = {}
    for i, row in enumerate(runs):
        for field in ("scenario_sha256", "observation_semantic_sha256"):
            key = (field, str(row[field]))
            if key in seen:
                a, b = root(i), root(seen[key])
                parent[max(a, b)] = min(a, b)
            else:
                seen[key] = i
    groups = sorted({root(i) for i in range(len(runs))})
    if len(groups) < 3:
        raise ValueError("split requires at least three independent run groups")
    Random(seed).shuffle(groups)
    held_out = max(1, len(groups) // 5)
    membership = {
        group: "test" if i < held_out else "validation" if i < 2 * held_out else "train"
        for i, group in enumerate(groups)
    }
    return {
        "format": "autocan-run-split",
        "format_version": 1,
        "dataset_semantic_sha256": meta["semantic_sha256"],
        "seed": seed,
        "assignments": [
            {
                "index": row["index"],
                "run_id": row["run_id"],
                "group": runs[root(i)]["index"],
                "split": membership[root(i)],
            }
            for i, row in enumerate(runs)
        ],
    }


def validate_split(meta: FactoryManifest, value: object) -> SplitManifest:
    """Reject overlap, omissions, changed identities, datasets and assignments."""
    data = object_value(value, "split manifest")
    expected = make_split(meta, seed=integer(data.get("seed"), "split seed"))
    if data != expected:
        raise ValueError("split manifest is incompatible, overlapping or incomplete")
    return expected


@dataclass(frozen=True, slots=True)
class FaultEpisode:
    fault_id: str
    fault_type: str
    start_ns: int
    end_ns: int
    can_ids: tuple[int, ...]
    censored: bool = False


@dataclass(frozen=True, slots=True)
class AlertEpisode:
    rule_id: str
    start_ns: int
    end_ns: int | None
    can_ids: tuple[int, ...]
    onsets: tuple[tuple[int, int], ...] = ()


class AlertCollector:
    """Union simultaneous message alerts into one causal episode per rule."""

    def __init__(self) -> None:
        self._active: dict[str, set[int]] = {rule: set() for rule in RULE_IDS}
        self._start: dict[str, int] = {}
        self._onsets: dict[str, dict[int, int]] = {rule: {} for rule in RULE_IDS}
        self._episodes: dict[str, list[AlertEpisode]] = {rule: [] for rule in RULE_IDS}
        self._count = 0

    def _append(self, rule: str, end_ns: int | None) -> None:
        if self._count >= MAX_ALERT_EPISODES:
            raise ValueError("benchmark alert episode quota exceeded")
        self._episodes[rule].append(
            AlertEpisode(
                rule,
                self._start.pop(rule),
                end_ns,
                tuple(sorted(self._onsets[rule])),
                tuple(sorted(self._onsets[rule].items())),
            )
        )
        self._onsets[rule].clear()
        self._count += 1

    def consume(self, at_ns: int, records: Iterable[dict[str, object]]) -> None:
        was_active = {rule: bool(active) for rule, active in self._active.items()}
        for record in records:
            if record["kind"] != "alert":
                continue
            rule, can_id = str(record["rule_id"]), integer(record["can_id"], "CAN ID")
            if record["state"] == "opened":
                self._active[rule].add(can_id)
                self._onsets[rule].setdefault(can_id, at_ns)
            else:
                self._active[rule].remove(can_id)
        for rule in RULE_IDS:
            if not was_active[rule] and self._active[rule]:
                self._start[rule] = at_ns
            elif was_active[rule] and not self._active[rule]:
                self._append(rule, at_ns)

    def finish(self) -> dict[str, list[AlertEpisode]]:
        for rule in RULE_IDS:
            if rule in self._start:
                self._append(rule, None)
        return self._episodes


@dataclass(frozen=True, slots=True)
class EpisodeMatch:
    fault_index: int
    alert_index: int
    latency_ns: int


@dataclass(frozen=True, slots=True)
class RuleResult:
    faults: tuple[FaultEpisode, ...]
    alerts: tuple[AlertEpisode, ...]
    matches: tuple[EpisodeMatch, ...]
    normal_duration_ns: int


def evaluate_rule(
    faults: list[FaultEpisode],
    alerts: list[AlertEpisode],
    *,
    grace_ns: int,
    normal_duration_ns: int,
) -> RuleResult:
    """Onset-ordered one-to-one matching, earliest-expiring eligible truth first.

    A rule episode already open before a fault gets no credit. Affected CAN IDs
    must intersect, and the first matching-ID onset must be in [start, end+grace).
    """
    integer(grace_ns, "matching grace")
    integer(normal_duration_ns, "normal exposure")
    if faults and normal_duration_ns:
        raise ValueError("normal exposure must come from entirely fault-free runs")
    used: set[int] = set()
    matches = []
    for alert_index in sorted(range(len(alerts)), key=lambda i: (alerts[i].start_ns, i)):
        alert = alerts[alert_index]
        candidates = []
        onsets = dict(alert.onsets)
        for i, fault in enumerate(faults):
            common = set(fault.can_ids) & set(alert.can_ids)
            if i in used or not common or alert.start_ns < fault.start_ns:
                continue
            at = min(onsets.get(can_id, alert.start_ns) for can_id in common)
            if fault.start_ns <= at < fault.end_ns + grace_ns:
                candidates.append((fault.end_ns + grace_ns, fault.start_ns, fault.fault_id, i, at))
        if candidates:
            _, _, _, index, at = min(candidates)
            used.add(index)
            matches.append(EpisodeMatch(index, alert_index, at - faults[index].start_ns))
    return RuleResult(tuple(faults), tuple(alerts), tuple(matches), normal_duration_ns)


class LatencyMetrics(TypedDict):
    mean: float | None
    p50: int | None
    p95: int | None


def _latency(values: list[int]) -> LatencyMetrics:
    ordered = sorted(values)
    return {
        "mean": sum(ordered) / len(ordered) if ordered else None,
        "p50": ordered[ceil(0.5 * len(ordered)) - 1] if ordered else None,
        "p95": ordered[ceil(0.95 * len(ordered)) - 1] if ordered else None,
    }


class FaultTypeMetrics(TypedDict):
    fault_episodes: int
    detected_episodes: int
    detection_rate: float
    detection_latency_ns: LatencyMetrics


class EpisodeMetrics(TypedDict):
    fault_episodes: int
    alert_episodes: int
    detected_episodes: int
    missed_episodes: int
    false_positive_episodes: int
    detection_rate: float | None
    episode_precision: float | None
    episode_f1: float | None
    normal_false_alarm_episodes: int
    normal_duration_ns: int
    false_alarms_per_hour: float | None
    detection_latency_ns: LatencyMetrics
    by_fault_type: dict[str, FaultTypeMetrics]


def summarize(results: Iterable[RuleResult]) -> EpisodeMetrics:
    faults = alarms = detected = normal_alarms = exposure = 0
    latencies: list[int] = []
    type_counts: dict[str, int] = {}
    type_latencies: dict[str, list[int]] = {}
    for result in results:
        faults += len(result.faults)
        alarms += len(result.alerts)
        detected += len(result.matches)
        exposure += result.normal_duration_ns
        if result.normal_duration_ns:
            normal_alarms += len(result.alerts)
        for fault in result.faults:
            type_counts[fault.fault_type] = type_counts.get(fault.fault_type, 0) + 1
            type_latencies.setdefault(fault.fault_type, [])
        for match in result.matches:
            latencies.append(match.latency_ns)
            type_latencies[result.faults[match.fault_index].fault_type].append(match.latency_ns)
    return {
        "fault_episodes": faults,
        "alert_episodes": alarms,
        "detected_episodes": detected,
        "missed_episodes": faults - detected,
        "false_positive_episodes": alarms - detected,
        "detection_rate": detected / faults if faults else None,
        "episode_precision": detected / alarms if alarms else None,
        "episode_f1": 2 * detected / (faults + alarms) if faults + alarms else None,
        "normal_false_alarm_episodes": normal_alarms,
        "normal_duration_ns": exposure,
        "false_alarms_per_hour": normal_alarms * 3_600_000_000_000 / exposure if exposure else None,
        "detection_latency_ns": _latency(latencies),
        "by_fault_type": {
            kind: {
                "fault_episodes": count,
                "detected_episodes": len(type_latencies[kind]),
                "detection_rate": len(type_latencies[kind]) / count,
                "detection_latency_ns": _latency(type_latencies[kind]),
            }
            for kind, count in sorted(type_counts.items())
        },
    }
