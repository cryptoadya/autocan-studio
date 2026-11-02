"""Hand-counted window metrics and alignment contracts shared by rules and ML."""

from dataclasses import replace
from typing import Literal

import pytest

from autocan.window_evaluation import (
    WindowKey,
    WindowScore,
    WindowTruth,
    baseline_window_scores,
    evaluate_windows,
)


def samples(
    labels: list[bool | None], scores: list[float | None]
) -> tuple[list[WindowTruth], list[WindowScore]]:
    keys = [
        WindowKey(i, "run", "validation", i * 100, (i + 1) * 100, label is not None)
        for i, label in enumerate(labels)
    ]
    return (
        [WindowTruth(key, label) for key, label in zip(keys, labels, strict=True)],
        [WindowScore(key, score) for key, score in zip(keys, scores, strict=True)],
    )


def test_hand_counted_metrics_pr_points_and_threshold_boundary() -> None:
    truth, scores = samples([False, True, True, False], [0.1, 0.4, 0.35, 0.8])
    result = evaluate_windows(truth, scores, model_id="example", split="validation", threshold=0.4)
    assert result["confusion_matrix"] == {
        "labels": ["normal", "anomaly"],
        "counts": [[1, 1], [1, 1]],
    }
    assert result["precision"] == result["recall"] == result["f1"] == 0.5
    assert result["average_precision"] == pytest.approx(7 / 12)
    curve = result["pr_curve"]
    assert curve["thresholds"] == [0.1, 0.35, 0.4, 0.8]
    assert curve["precision"] == pytest.approx([0.5, 2 / 3, 0.5, 0, 1])
    assert curve["recall"] == [1, 1, 0.5, 0, 0]
    strict = evaluate_windows(
        truth, scores, model_id="example", split="validation", threshold=0.4, comparison="gt"
    )
    assert strict["confusion_matrix"]["counts"] == [[1, 1], [2, 0]]
    assert strict["average_precision"] == result["average_precision"]
    assert strict["pr_curve"] == curve  # ranking always sweeps score >= threshold


def test_tied_scores_are_grouped_and_independent_of_input_order() -> None:
    truth, scores = samples([True, False, True, False], [0.5] * 4)
    first = evaluate_windows(truth, scores, model_id="tied", split="validation", threshold=0.5)
    second = evaluate_windows(
        reversed(truth), reversed(scores), model_id="tied", split="validation", threshold=0.5
    )
    assert first == second
    assert first["average_precision"] == 0.5
    assert first["pr_curve"] == {"thresholds": [0.5], "precision": [0.5, 1], "recall": [1, 0]}


def test_ap_weights_recall_increments_across_mixed_tie_groups() -> None:
    truth, scores = samples([True, False, True, True, False], [0.9, 0.8, 0.8, 0.4, 0.4])
    result = evaluate_windows(truth, scores, model_id="ties", split="validation", threshold=0.8)
    assert result["average_precision"] == pytest.approx(34 / 45)
    assert result["pr_curve"]["thresholds"] == [0.4, 0.8, 0.9]
    assert result["pr_curve"]["precision"] == pytest.approx([3 / 5, 2 / 3, 1, 1])
    assert result["pr_curve"]["recall"] == pytest.approx([1, 2 / 3, 1 / 3, 0])


def test_higher_scores_mean_anomaly_even_when_all_scores_are_negative() -> None:
    truth, scores = samples([False, True], [-10.0, -1.0])
    result = evaluate_windows(truth, scores, model_id="signed", split="validation", threshold=-5)
    assert result["precision"] == result["recall"] == result["f1"] == 1
    assert result["average_precision"] == 1


@pytest.mark.parametrize(
    ("labels", "values", "precision", "recall", "f1", "ap"),
    [
        ([], [], None, None, None, None),
        ([False, False], [0.1, 0.2], None, None, None, None),
        ([False, False], [1.0, 1.0], 0.0, None, 0.0, None),
        ([True, True], [0.1, 0.2], None, 0.0, 0.0, 1.0),
        ([True, True], [1.0, 1.0], 1.0, 1.0, 1.0, 1.0),
    ],
)
def test_empty_single_class_and_no_predicted_positive_denominators(
    labels: list[bool],
    values: list[float],
    precision: float | None,
    recall: float | None,
    f1: float | None,
    ap: float | None,
) -> None:
    truth, scores = samples(list(labels), list(values))
    result = evaluate_windows(truth, scores, model_id="empty", split="validation", threshold=0.5)
    assert [result[k] for k in ("precision", "recall", "f1", "average_precision")] == [
        precision,
        recall,
        f1,
        ap,
    ]
    if ap is None:
        assert result["pr_curve"] == {"thresholds": [], "precision": [], "recall": []}


def test_split_filter_and_warmup_exclusion_do_not_change_denominators() -> None:
    truth, scores = samples([None, True, False], [None, 0.9, 0.1])
    other = WindowKey(3, "test-run", "test", 0, 100, True)
    truth.append(WindowTruth(other, True))
    scores.append(WindowScore(other, -100))
    result = evaluate_windows(truth, scores, model_id="frozen", split="validation", threshold=0.5)
    assert result["input_windows"] == 4
    assert result["selected_windows"] == 3
    assert result["excluded_warmup_windows"] == 1
    assert result["evaluated_windows"] == 2
    assert result["positive_windows"] == result["negative_windows"] == 1
    assert result["precision"] == result["recall"] == result["f1"] == 1


def test_rejects_missing_extra_shuffled_duplicate_or_cross_split_rows() -> None:
    truth, scores = samples([True, False], [0.9, 0.1])
    for predictions in (scores[:-1], [*scores, scores[0]], list(reversed(scores))):
        with pytest.raises(ValueError):
            evaluate_windows(truth, predictions, model_id="bad", split="validation", threshold=0.5)
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_windows(truth * 2, scores * 2, model_id="bad", split="validation", threshold=0.5)
    changed = replace(truth[1].key, split="test")
    with pytest.raises(ValueError, match="split"):
        evaluate_windows(
            [truth[0], WindowTruth(changed, False)],
            [scores[0], WindowScore(changed, 0.1)],
            model_id="bad",
            split="validation",
            threshold=0.5,
        )


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), -float("inf"), True])
def test_invalid_eligible_scores_fail_instead_of_dropping_windows(bad: float | None) -> None:
    truth, scores = samples([True], [bad])
    with pytest.raises(ValueError, match="score"):
        evaluate_windows(truth, scores, model_id="bad", split="validation", threshold=0.5)


def test_quota_and_invalid_labels_are_rejected() -> None:
    truth, scores = samples([True, False], [0.9, 0.1])
    with pytest.raises(ValueError, match="quota"):
        evaluate_windows(
            truth, scores, model_id="bad", split="validation", threshold=0.5, max_windows=1
        )
    with pytest.raises(ValueError, match="label"):
        evaluate_windows(
            [WindowTruth(truth[0].key, None)],
            scores[:1],
            model_id="bad",
            split="validation",
            threshold=0.5,
        )


def test_rule_adapter_uses_only_raw_features_and_preserves_threshold_semantics() -> None:
    from autocan.features import FeatureConfig

    config = FeatureConfig(rpm_derivative_threshold=0, wheel_residual_threshold=5)
    features: dict[str, float | None] = {
        "can_512_age_ns": 100.0,
        "can_512_timeout_ns": 100.0,
        "can_528_age_ns": 50.0,
        "can_528_timeout_ns": 200.0,
        "rpm_derivative_max": None,
        "wheel_residual_max": 5.0,
    }
    results = baseline_window_scores(features, config)
    expected: dict[str, tuple[float, float, Literal["ge", "gt"]]] = {
        "heartbeat_timeout": (1.0, 1.0, "ge"),
        "rpm_derivative": (0.0, 0.0, "gt"),
        "wheel_residual": (5.0, 5.0, "gt"),
    }
    for rule, (score, threshold, comparison) in expected.items():
        evidence = results[rule]
        assert (evidence.score, evidence.threshold, evidence.comparison) == (
            score,
            threshold,
            comparison,
        )
        truth, predictions = samples([True], [evidence.score])
        metrics = evaluate_windows(
            truth,
            predictions,
            model_id=rule,
            split="validation",
            threshold=evidence.threshold,
            comparison=evidence.comparison,
        )
        assert metrics["recall"] == (1 if rule == "heartbeat_timeout" else 0)
    with pytest.raises(ValueError, match="heartbeat"):
        baseline_window_scores({"rpm_derivative_max": 0.0, "wheel_residual_max": 0.0}, config)
