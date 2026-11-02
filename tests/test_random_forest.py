"""Supervised fit scope, target fingerprints and positive-class scores."""

import pickle
from dataclasses import replace
from hashlib import sha256

import numpy as np
import pytest

from autocan.preprocessing import FitSample, Preprocessor, fit_training
from autocan.random_forest import fit_random_forest
from autocan.training_artifacts import PreparedWindow
from autocan.validation import canonical_bytes
from autocan.window_evaluation import WindowKey


def rows() -> list[PreparedWindow]:
    return [
        PreparedWindow(WindowKey(i, f"r{i}", split, 0, 100, eligible), (value,), label, normal)
        for i, (split, value, label, normal, eligible) in enumerate(
            [
                ("train", 0.0, False, True, True),
                ("train", 1.0, False, False, True),  # negative segment of a fault run: included
                ("train", 2.0, False, True, True),
                ("train", 3.0, True, False, True),
                ("train", 1e50, None, False, False),
                ("validation", 1e50, True, False, True),
                ("test", 1e50, True, False, True),
                ("ood_fault", 1e50, True, False, True),
            ]
        )
    ]


def state(windows: list[PreparedWindow]) -> Preprocessor:
    return fit_training(
        [
            FitSample(r.key.row_index, r.key.split, r.key.eligible, r.normal_run, r.features)
            for r in windows
        ],
        ("x",),
        normal_only=False,
    )


def test_fit_uses_all_labeled_eligible_train_windows_and_no_held_out_values() -> None:
    windows = rows()
    frozen = state(windows)
    first = fit_random_forest(windows, frozen, seed=42)
    changed = windows[:4] + [replace(r, features=(float("nan"),), label=False) for r in windows[4:]]
    second = fit_random_forest(changed, frozen, seed=42)
    assert first.fit_rows == frozen.fit_rows == 4
    assert first.fit_positive_windows == 1
    assert first.fit_rows_sha256 == frozen.fit_rows_sha256
    expected = sha256(
        b"".join(
            canonical_bytes({"row_index": r.key.row_index, "label": r.label}) + b"\n"
            for r in windows[:4]
        )
    ).hexdigest()
    assert first.label_rows_sha256 == expected
    assert pickle.dumps(first.estimator, protocol=5) == pickle.dumps(second.estimator, protocol=5)
    assert first.estimator.class_weight == "balanced"
    assert first.estimator.classes_.tolist() == [0, 1]
    matrix = np.asarray([frozen.transform(r.features, columns=frozen.columns) for r in windows[:4]])
    expected_scores = first.estimator.predict_proba(matrix)[:, 1]
    assert first.score(windows[:4]) == pytest.approx(expected_scores)
    assert all(
        tree.tree_.max_depth <= 12 and tree.tree_.n_leaves <= 256
        for tree in first.estimator.estimators_
    )


def test_fit_rejects_wrong_scope_missing_labels_duplicates_and_quota() -> None:
    windows = rows()
    frozen = state(windows)
    with pytest.raises(ValueError, match="all-train"):
        fit_random_forest(windows, replace(frozen, normal_only=True))
    with pytest.raises(ValueError, match="label"):
        fit_random_forest([replace(windows[0], label=None), *windows[1:]], frozen)
    with pytest.raises(ValueError, match="duplicate"):
        fit_random_forest([*windows, windows[0]], frozen)
    with pytest.raises(ValueError, match="quota"):
        fit_random_forest(windows, frozen, max_values=3)
    with pytest.raises(ValueError, match="fingerprint"):
        fit_random_forest(windows[1:], frozen)


@pytest.mark.parametrize("positive", [False, True])
def test_fit_requires_both_train_classes(positive: bool) -> None:
    windows = [replace(r, label=positive) for r in rows()[:4]]
    with pytest.raises(ValueError, match="train.*classes"):
        fit_random_forest(windows, state(windows))
