"""Common binary window metrics; detector scores and evaluator truth stay separate."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import groupby
from math import fsum
from sys import float_info
from typing import Literal, TypedDict

from autocan.features import FeatureConfig
from autocan.validation import integer, number

WINDOW_EVALUATOR_VERSION = "binary-windows-1"
MAX_EVALUATION_WINDOWS = 100_000
Comparison = Literal["ge", "gt"]


def _finite(value: object, name: str) -> float:
    return number(value, name, minimum=-float_info.max, maximum=float_info.max)


def _identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


@dataclass(frozen=True, slots=True)
class WindowKey:
    """Metadata from windows.parquet; never append these fields to model inputs."""

    row_index: int
    run_id: str
    split: str
    start_ns: int
    end_ns: int
    eligible: bool = True

    def __post_init__(self) -> None:
        integer(self.row_index, "window row index")
        _identifier(self.run_id, "window run ID")
        _identifier(self.split, "window split")
        integer(self.start_ns, "window start")
        integer(self.end_ns, "window end", minimum=self.start_ns + 1)
        if type(self.eligible) is not bool:
            raise ValueError("window eligibility must be boolean")


@dataclass(frozen=True, slots=True)
class WindowTruth:
    key: WindowKey
    label: bool | None


@dataclass(frozen=True, slots=True)
class WindowScore:
    key: WindowKey
    score: float | None


class ConfusionMatrix(TypedDict):
    labels: list[str]
    counts: list[list[int]]


class PrecisionRecallCurve(TypedDict):
    thresholds: list[float]
    precision: list[float]
    recall: list[float]


class WindowMetrics(TypedDict):
    evaluator_version: str
    model_id: str
    split: str
    threshold: float
    comparison: Comparison
    input_windows: int
    selected_windows: int
    excluded_warmup_windows: int
    evaluated_windows: int
    positive_windows: int
    negative_windows: int
    confusion_matrix: ConfusionMatrix
    precision: float | None
    recall: float | None
    f1: float | None
    average_precision: float | None
    pr_curve: PrecisionRecallCurve


def _ranking(
    rows: list[tuple[float, bool]], positives: int
) -> tuple[float | None, PrecisionRecallCurve]:
    curve: PrecisionRecallCurve = {"thresholds": [], "precision": [], "recall": []}
    if not positives:
        return None, curve
    tp = count = 0
    increments: list[float] = []
    # One point per distinct score: tie order cannot alter AP.
    for score, group in groupby(sorted(rows, reverse=True), key=lambda row: row[0]):
        added = 0
        for _, label in group:
            count += 1
            added += int(label)
        tp += added
        precision = tp / count
        increments.append(added / positives * precision)
        curve["thresholds"].append(score)
        curve["precision"].append(precision)
        curve["recall"].append(tp / positives)
    curve["thresholds"].reverse()
    curve["precision"].reverse()
    curve["recall"].reverse()
    curve["precision"].append(1.0)
    curve["recall"].append(0.0)
    return fsum(increments), curve


def evaluate_windows(
    truth: Iterable[WindowTruth],
    scores: Iterable[WindowScore],
    *,
    model_id: str,
    split: str,
    threshold: float,
    comparison: Comparison = "ge",
    max_windows: int = MAX_EVALUATION_WINDOWS,
) -> WindowMetrics:
    """Pool one split's eligible windows with an externally frozen threshold.

    Higher scores always mean more anomalous. Strict, ordered key alignment is
    required even on excluded rows; missing eligible predictions fail closed.
    PR sweeps >= at every distinct score, independently of the operating point.
    No fitting, threshold search, label projection or artifact loading occurs here.
    """
    _identifier(model_id, "model ID")
    _identifier(split, "split")
    threshold = _finite(threshold, "threshold")
    if comparison not in ("ge", "gt"):
        raise ValueError("comparison must be ge or gt")
    if integer(max_windows, "window quota", minimum=1) > MAX_EVALUATION_WINDOWS:
        raise ValueError("window quota exceeds evaluator limit")
    indices: set[int] = set()
    identities: set[tuple[str, int, int]] = set()
    run_splits: dict[str, str] = {}
    rows: list[tuple[float, bool]] = []
    selected = excluded = positives = tp = fp = 0
    for target, prediction in zip(truth, scores, strict=True):
        if len(indices) >= max_windows:
            raise ValueError("evaluation window quota exceeded")
        key = target.key
        if key != prediction.key:
            raise ValueError("window score/truth key alignment mismatch")
        identity = (key.run_id, key.start_ns, key.end_ns)
        if key.row_index in indices or identity in identities:
            raise ValueError("duplicate window identity or row index")
        indices.add(key.row_index)
        identities.add(identity)
        if run_splits.setdefault(key.run_id, key.split) != key.split:
            raise ValueError("a run cannot span multiple splits")
        if (key.eligible and type(target.label) is not bool) or (
            not key.eligible and target.label is not None
        ):
            raise ValueError("window label must be boolean when eligible, otherwise null")
        score = None if prediction.score is None else _finite(prediction.score, "window score")
        if key.eligible and score is None:
            raise ValueError("eligible window score must be finite, never missing")
        if key.split != split:
            continue
        selected += 1
        if not key.eligible:
            excluded += 1
            continue
        assert score is not None and target.label is not None
        label = target.label
        rows.append((score, label))
        positives += int(label)
        predicted = score >= threshold if comparison == "ge" else score > threshold
        tp += int(label and predicted)
        fp += int(not label and predicted)
    count = len(rows)
    fn, tn = positives - tp, count - positives - fp
    ap, curve = _ranking(rows, positives)
    return {
        "evaluator_version": WINDOW_EVALUATOR_VERSION,
        "model_id": model_id,
        "split": split,
        "threshold": threshold,
        "comparison": comparison,
        "input_windows": len(indices),
        "selected_windows": selected,
        "excluded_warmup_windows": excluded,
        "evaluated_windows": count,
        "positive_windows": positives,
        "negative_windows": count - positives,
        "confusion_matrix": {"labels": ["normal", "anomaly"], "counts": [[tn, fp], [fn, tp]]},
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / positives if positives else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
        "average_precision": ap,
        "pr_curve": curve,
    }


@dataclass(frozen=True, slots=True)
class BaselineWindowScore:
    score: float
    threshold: float
    comparison: Comparison


def baseline_window_scores(
    features: Mapping[str, float | None], config: FeatureConfig
) -> dict[str, BaselineWindowScore]:
    """Stateless window evidence from raw, unscaled M9 numeric feature rows.

    Missing RPM/wheel evidence is assigned zero (no current anomaly evidence),
    preserving every eligible window in all rules' denominators. This does not
    change the M8 detector's separately latched alert episodes.
    """
    ratios = []
    for name in features:
        if name.startswith("can_") and name.endswith("_age_ns"):
            age = number(features[name], "heartbeat age", minimum=0, maximum=1e100)
            timeout = number(
                features.get(name.removesuffix("age_ns") + "timeout_ns"),
                "heartbeat timeout",
                minimum=1,
                maximum=1e100,
            )
            ratios.append(age / timeout)
    if not ratios:
        raise ValueError("heartbeat features require subscribed CAN ages/timeouts")

    def evidence(name: str) -> float:
        if name not in features:
            raise ValueError(f"missing baseline feature: {name}")
        value = features[name]
        return 0.0 if value is None else number(value, name, minimum=0, maximum=1e100)

    return {
        "heartbeat_timeout": BaselineWindowScore(max(ratios), 1.0, "ge"),
        "rpm_derivative": BaselineWindowScore(
            evidence("rpm_derivative_max"), config.rpm_derivative_threshold, "gt"
        ),
        "wheel_residual": BaselineWindowScore(
            evidence("wheel_residual_max"), config.wheel_residual_threshold, "gt"
        ),
    }
