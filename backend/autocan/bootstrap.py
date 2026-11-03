"""Paired percentile bootstrap of pooled fixed-score metrics over whole runs."""

from collections.abc import Sequence
from hashlib import sha256
from math import ceil
from typing import TypedDict

import numpy as np
import numpy.typing as npt

from autocan.saved_evaluation import ScoredRun
from autocan.validation import canonical_bytes, integer
from autocan.window_evaluation import Comparison

BOOTSTRAP_VERSION = "saved-score-run-bootstrap-1"
DEFAULT_REPLICATES = 2000
MAX_REPLICATES = 10_000
Values = dict[str, float | None]
Weights = npt.NDArray[np.int64]


class Interval(TypedDict):
    estimate: float | None
    lower: float | None
    upper: float | None
    valid_replicates: int
    undefined_replicates: int
    status: str


class BootstrapRow(TypedDict):
    split: str
    model_id: str
    threshold: float
    comparison: Comparison
    runs: int
    window: dict[str, Interval]
    event: dict[str, Interval]


class FaultRow(TypedDict):
    split: str
    model_id: str
    fault_type: str
    contributing_runs: int
    event: dict[str, Interval]


class BootstrapResult(TypedDict):
    rows: list[BootstrapRow]
    fault_type_rows: list[FaultRow]
    resampling: list[dict[str, object]]


class WindowPool:
    """Sort scores once; multiply entire runs, including tied-score groups."""

    def __init__(
        self, runs: list[ScoredRun], model: str, threshold: float, comparison: Comparison
    ) -> None:
        cells = []
        ranked: list[tuple[float, bool, int]] = []
        for i, run in enumerate(runs):
            tn = fp = fn = tp = 0
            for key, label, value in zip(run.keys, run.labels, run.scores[model], strict=True):
                if not key.eligible:
                    continue
                assert type(label) is bool
                positive = value >= threshold if comparison == "ge" else value > threshold
                tn += int(not label and not positive)
                fp += int(not label and positive)
                fn += int(label and not positive)
                tp += int(label and positive)
                ranked.append((value, label, i))
            cells.append((tn, fp, fn, tp))
        self.cells = np.array(cells, dtype=np.int64)
        ranked.sort(key=lambda r: r[0], reverse=True)
        self.run_indices = np.array([r[2] for r in ranked], dtype=np.int64)
        self.labels = np.array([r[1] for r in ranked], dtype=np.int64)
        self.starts = np.array(
            [i for i, r in enumerate(ranked) if i == 0 or r[0] != ranked[i - 1][0]],
            dtype=np.int64,
        )

    def values(self, weights: Weights) -> Values:
        tn, fp, fn, tp = (int(v) for v in weights @ self.cells)
        positives = tp + fn
        ap = None
        if positives:
            multiplicity = weights[self.run_indices]
            group_count = np.add.reduceat(multiplicity, self.starts)
            group_tp = np.add.reduceat(multiplicity * self.labels, self.starts)
            cumulative = np.cumsum(group_count)
            precision = np.divide(
                np.cumsum(group_tp),
                cumulative,
                out=np.zeros(len(cumulative)),
                where=cumulative != 0,
            )
            ap = float(np.sum(group_tp * precision) / positives)
        return {
            "tn": tn,
            "fp": fp,
            "fn": fn,
            "tp": tp,
            "evaluated_windows": tn + fp + fn + tp,
            "positive_windows": positives,
            "negative_windows": tn + fp,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / positives if positives else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
            "average_precision": ap,
        }


def event_values(
    runs: list[ScoredRun], model: str, weights: Sequence[int], kind: str | None = None
) -> Values:
    faults = detected = alarms = normal_alarms = exposure = 0
    unscored = left = right = 0
    latencies: list[tuple[int, int]] = []
    for run, weight in zip(runs, weights, strict=True):
        if not weight:
            continue
        events = run.events[model]
        result = events.result
        faults += weight * sum(kind is None or f.fault_type == kind for f in result.faults)
        unscored += weight * sum(
            kind is None or f.fault_type == kind for f in events.unscored_faults
        )
        selected_ids = {f.fault_id for f in result.faults if kind is None or f.fault_type == kind}
        left += weight * sum(f in selected_ids for f in events.left_censored_fault_ids)
        right += weight * sum(f in selected_ids for f in events.right_censored_fault_ids)
        for match in result.matches:
            if kind is None or result.faults[match.fault_index].fault_type == kind:
                detected += weight
                latencies.append((match.latency_ns, weight))
        if kind is None:
            alarms += weight * len(result.alerts)
            exposure += weight * result.normal_duration_ns
            normal_alarms += weight * len(result.alerts) if result.normal_duration_ns else 0
    values: Values = {
        "fault_episodes": faults,
        "detected_episodes": detected,
        "missed_episodes": faults - detected,
        "unscored_fault_episodes": unscored,
        "left_censored_fault_episodes": left,
        "right_censored_fault_episodes": right,
        "detection_rate": detected / faults if faults else None,
        "latency_mean_ns": sum(v * w for v, w in latencies) / detected if detected else None,
        "latency_p50_ns": None,
        "latency_p95_ns": None,
    }
    ordered = sorted(latencies)
    for name, quantile in (("latency_p50_ns", 0.5), ("latency_p95_ns", 0.95)):
        rank, count = ceil(quantile * detected), 0
        for value, weight in ordered:
            count += weight
            if count >= rank:
                values[name] = value
                break
    if kind is None:
        values.update(
            alert_episodes=alarms,
            false_positive_episodes=alarms - detected,
            episode_precision=detected / alarms if alarms else None,
            episode_f1=2 * detected / (faults + alarms) if faults + alarms else None,
            normal_false_alarm_episodes=normal_alarms,
            normal_duration_ns=exposure,
            false_alarms_per_hour=normal_alarms * 3_600_000_000_000 / exposure
            if exposure
            else None,
        )
    return values


def _intervals(estimate: Values, draws: list[Values], runs: int) -> dict[str, Interval]:
    intervals = {}
    for name, point in estimate.items():
        valid = [value for row in draws if (value := row[name]) is not None]
        lower = upper = None
        if runs < 2:
            status = "insufficient_runs"
        elif len(valid) < 2:
            status = "undefined" if not valid else "insufficient_valid_replicates"
        else:
            lower, upper = (float(v) for v in np.quantile(valid, [0.025, 0.975], method="linear"))
            status = "partial_undefined" if len(valid) < len(draws) else "ok"
        intervals[name] = Interval(
            estimate=point,
            lower=lower,
            upper=upper,
            valid_replicates=len(valid),
            undefined_replicates=len(draws) - len(valid),
            status=status,
        )
    return intervals


def bootstrap_runs(
    runs: list[ScoredRun],
    policies: dict[str, tuple[float, Comparison]],
    *,
    replicates: int = DEFAULT_REPLICATES,
    seed: int = 42,
) -> BootstrapResult:
    """Uniform n-out-of-n run draws per partition, shared by all detectors/types."""
    if integer(replicates, "bootstrap replicates", minimum=2) > MAX_REPLICATES:
        raise ValueError("bootstrap replicates exceed 10000")
    integer(seed, "bootstrap seed")
    if len({r.run_id for r in runs}) != len(runs):
        raise ValueError("duplicate run identity or cross-partition run")
    rows: list[BootstrapRow] = []
    fault_rows: list[FaultRow] = []
    resampling = []
    for part in sorted({r.split for r in runs}):
        selected = sorted((r for r in runs if r.split == part), key=lambda r: r.run_id)
        n = len(selected)
        # Partition-local deterministic streams are independent of input order/other partitions.
        entropy = int.from_bytes(sha256(part.encode()).digest()[:8], "big")
        rng = np.random.Generator(np.random.PCG64(np.random.SeedSequence([seed, entropy])))
        weights = [np.bincount(rng.integers(n, size=n), minlength=n) for _ in range(replicates)]
        draw_hash = sha256()
        for weight in weights:
            draw_hash.update(canonical_bytes(weight.tolist()) + b"\n")
        resampling.append(
            {
                "split": part,
                "runs": [r.run_id for r in selected],
                "run_count": n,
                "draws_sha256": draw_hash.hexdigest(),
            }
        )
        ones = np.ones(n, dtype=np.int64)
        for model, (threshold, comparison) in policies.items():
            pool = WindowPool(selected, model, threshold, comparison)
            rows.append(
                BootstrapRow(
                    split=part,
                    model_id=model,
                    threshold=threshold,
                    comparison=comparison,
                    runs=n,
                    window=_intervals(pool.values(ones), [pool.values(w) for w in weights], n),
                    event=_intervals(
                        event_values(selected, model, ones.tolist()),
                        [event_values(selected, model, w.tolist()) for w in weights],
                        n,
                    ),
                )
            )
            kinds = sorted(
                {
                    f.fault_type
                    for r in selected
                    for f in (*r.events[model].result.faults, *r.events[model].unscored_faults)
                }
            )
            for kind in kinds:
                contributors = sum(
                    any(
                        f.fault_type == kind
                        for f in (*r.events[model].result.faults, *r.events[model].unscored_faults)
                    )
                    for r in selected
                )
                fault_rows.append(
                    FaultRow(
                        split=part,
                        model_id=model,
                        fault_type=kind,
                        contributing_runs=contributors,
                        event=_intervals(
                            event_values(selected, model, ones.tolist(), kind),
                            [event_values(selected, model, w.tolist(), kind) for w in weights],
                            contributors,
                        ),
                    )
                )
    return {"rows": rows, "fault_type_rows": fault_rows, "resampling": resampling}
