"""Train/validation isolation and hand-counted threshold selection."""

import pickle
from dataclasses import replace
from hashlib import sha256

import numpy as np
import pytest

from autocan.isolation_forest import choose_validation_threshold, fit_isolation_forest
from autocan.preprocessing import FitSample, Preprocessor, fit_training
from autocan.training_artifacts import PreparedWindow
from autocan.validation import canonical_bytes
from autocan.window_evaluation import WindowKey


def window(
    index: int,
    split: str,
    value: float,
    *,
    normal: bool = True,
    label: bool | None = False,
    eligible: bool = True,
) -> PreparedWindow:
    return PreparedWindow(
        WindowKey(index, f"run-{index}", split, 0, 100, eligible), (value,), label, normal
    )


def state(rows: list[PreparedWindow]) -> Preprocessor:
    return fit_training(
        [
            FitSample(r.key.row_index, r.key.split, r.key.eligible, r.normal_run, r.features)
            for r in rows
        ],
        ("x",),
        normal_only=True,
    )


def test_fit_uses_only_eligible_windows_from_entirely_normal_train_runs() -> None:
    rows = [
        window(0, "train", 0),
        window(1, "train", 2),
        window(2, "train", 1e50, normal=False, label=False),
        window(3, "train", 1e50, label=None, eligible=False),
        window(4, "validation", 1e50),
        window(5, "test", 1e50),
        window(6, "ood_normal", 1e50),
    ]
    frozen = state(rows)
    first = fit_isolation_forest(iter(rows), frozen, seed=42)
    changed = rows[:2] + [replace(r, features=(float("nan"),), label=True) for r in rows[2:]]
    second = fit_isolation_forest(changed, frozen, seed=42)
    assert first.fit_rows == 2
    assert first.fit_rows_sha256 == frozen.fit_rows_sha256
    assert first.fit_rows_sha256 == sha256(b"0\n1\n").hexdigest()
    assert first.estimator.max_samples_ == 2
    assert pickle.dumps(first.estimator, protocol=5) == pickle.dumps(second.estimator, protocol=5)
    expected = -first.estimator.score_samples(np.array([[-1.0], [1.0]]))
    assert first.score(rows[:2]) == pytest.approx(expected)


def test_fit_rejects_wrong_preprocessing_scope_empty_fit_and_quota() -> None:
    rows = [window(0, "train", 0), window(1, "train", 2)]
    frozen = state(rows)
    with pytest.raises(ValueError, match="normal"):
        fit_isolation_forest(rows, replace(frozen, normal_only=False))
    with pytest.raises(ValueError, match="normal train"):
        fit_isolation_forest([window(0, "test", 0)], frozen)
    with pytest.raises(ValueError, match="quota"):
        fit_isolation_forest(rows, frozen, max_values=1)
    with pytest.raises(ValueError, match="fingerprint|scope"):
        fit_isolation_forest(rows[:1], frozen)


def test_threshold_uses_only_eligible_validation_labels_and_groups_ties() -> None:
    selected = [
        (window(0, "validation", 0, normal=False, label=True), 0.9),
        (window(1, "validation", 0), 0.5),
        (window(2, "validation", 0), 0.5),
        (window(3, "validation", 0, normal=False, label=True), 0.5),
    ]
    first = choose_validation_threshold(selected)
    # F1 is 2/3 at both cutoffs; the predeclared tie-break chooses 0.9.
    assert first.threshold == 0.9
    assert first.validation_f1 == pytest.approx(2 / 3)
    assert first.windows == 4 and first.positive_windows == 2 and first.candidates == 2
    expected = sha256(b"".join(canonical_bytes(i) + b"\n" for i in range(4))).hexdigest()
    assert first.row_indices_sha256 == expected
    ignored = [
        (window(4, "train", 0, label=True), float("nan")),
        (window(5, "test", 0, label=True), float("nan")),
        (window(6, "ood_fault", 0, label=True), float("nan")),
        (window(7, "validation", 0, eligible=False, label=None), float("nan")),
    ]
    assert choose_validation_threshold([*selected, *ignored]) == first


@pytest.mark.parametrize("labels", [[], [False, False], [True, True]])
def test_threshold_requires_both_validation_classes(labels: list[bool]) -> None:
    with pytest.raises(ValueError, match="validation.*classes"):
        choose_validation_threshold(
            [
                (window(i, "validation", 0, normal=not label, label=label), float(i))
                for i, label in enumerate(labels)
            ]
        )


def test_threshold_rejects_missing_labels_nonfinite_scores_and_duplicate_rows() -> None:
    positive = window(0, "validation", 0, label=True, normal=False)
    negative = window(1, "validation", 0)
    for rows in (
        [(replace(positive, label=None), 0.9), (negative, 0.1)],
        [(positive, float("nan")), (negative, 0.1)],
        [(positive, 0.9), (negative, 0.1), (positive, 0.9)],
    ):
        with pytest.raises(ValueError):
            choose_validation_threshold(rows)
