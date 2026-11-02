"""Normal-train-only IsolationForest and validation-only operating-point selection."""

from array import array
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from itertools import groupby

import numpy as np
from sklearn.ensemble import IsolationForest

from autocan.preprocessing import MAX_FIT_VALUES, Preprocessor
from autocan.training_artifacts import PreparedWindow
from autocan.training_data import MAX_ROWS
from autocan.validation import canonical_bytes, integer, number

MODEL_ID = "isolation_forest"
MODEL_VERSION = "isolation-forest-normal-train-1"


@dataclass(frozen=True, slots=True)
class FittedForest:
    estimator: IsolationForest
    preprocessing: Preprocessor
    fit_rows: int
    fit_rows_sha256: str
    seed: int

    def score(self, rows: Sequence[PreparedWindow]) -> list[float]:
        """Higher means anomaly; sklearn's learned contamination offset is unused."""
        if not rows:
            return []
        matrix = np.asarray(
            [
                self.preprocessing.transform(row.features, columns=self.preprocessing.columns)
                for row in rows
            ],
            dtype=np.float64,
        )
        scores = -self.estimator.score_samples(matrix)
        return [number(float(value), "anomaly score", minimum=0, maximum=1) for value in scores]


def fit_isolation_forest(
    rows: Iterable[PreparedWindow],
    state: Preprocessor,
    *,
    seed: int = 42,
    max_values: int = MAX_FIT_VALUES,
) -> FittedForest:
    if integer(seed, "model seed") > 2**32 - 1:
        raise ValueError("model seed must fit an unsigned 32-bit integer")
    if not state.normal_only:
        raise ValueError("IsolationForest requires frozen normal-train preprocessing")
    if integer(max_values, "model fit quota", minimum=1) > MAX_FIT_VALUES:
        raise ValueError("model fit quota exceeds supported limit")
    values = array("d")
    indices: set[int] = set()
    digest = sha256()
    for row in rows:
        if row.key.split != "train" or not row.key.eligible or not row.normal_run:
            continue
        if row.label is not False or row.key.row_index in indices:
            raise ValueError("invalid or duplicate normal train window")
        if len(values) + len(state.columns) > max_values:
            raise ValueError("model fit value quota exceeded")
        indices.add(row.key.row_index)
        digest.update(canonical_bytes(row.key.row_index) + b"\n")
        values.extend(state.transform(row.features, columns=state.columns))
    if not indices:
        raise ValueError("no eligible normal train windows")
    if len(indices) != state.fit_rows or digest.hexdigest() != state.fit_rows_sha256:
        raise ValueError("model/preprocessing fit scope fingerprint mismatch")
    estimator = IsolationForest(
        n_estimators=100,
        max_samples="auto",
        contamination="auto",
        max_features=1.0,
        bootstrap=False,
        n_jobs=1,
        random_state=seed,
        verbose=0,
        warm_start=False,
    )
    # A view avoids another float64 copy; sklearn's internal float32 copy is bounded too.
    matrix = np.frombuffer(values, dtype=np.float64).reshape(len(indices), len(state.columns))
    estimator.fit(matrix)
    return FittedForest(estimator, state, len(indices), digest.hexdigest(), seed)


@dataclass(frozen=True, slots=True)
class ThresholdSelection:
    threshold: float
    validation_f1: float
    windows: int
    positive_windows: int
    candidates: int
    row_indices_sha256: str


def choose_validation_threshold(
    rows: Iterable[tuple[PreparedWindow, float]],
) -> ThresholdSelection:
    """Maximize pooled validation F1 with >=; ties choose the highest threshold.

    Filtering precedes access to labels/scores. Train, test, OOD and warm-up
    values cannot influence either the candidates or their selection.
    """
    selected: list[tuple[float, bool]] = []
    indices: set[int] = set()
    digest = sha256()
    for row, value in rows:
        if row.key.split != "validation" or not row.key.eligible:
            continue
        if len(selected) >= MAX_ROWS:
            raise ValueError("validation threshold window quota exceeded")
        if type(row.label) is not bool or row.key.row_index in indices:
            raise ValueError("invalid or duplicate validation window label")
        score = number(value, "validation score", minimum=0, maximum=1)
        indices.add(row.key.row_index)
        digest.update(canonical_bytes(row.key.row_index) + b"\n")
        selected.append((score, row.label))
    positives = sum(label for _, label in selected)
    if not 0 < positives < len(selected):
        raise ValueError("validation threshold selection requires both classes")
    tp = predicted = candidates = 0
    best_tp, best_denominator = -1, 1
    threshold = 0.0
    for score, group in groupby(sorted(selected, reverse=True), key=lambda item: item[0]):
        for _, label in group:
            predicted += 1
            tp += int(label)
        candidates += 1
        denominator = positives + predicted
        # Integer comparison avoids rounding-dependent ties. Descending order
        # plus strict improvement implements the predeclared highest-cutoff tie-break.
        if tp * best_denominator > best_tp * denominator:
            threshold, best_tp, best_denominator = score, tp, denominator
    return ThresholdSelection(
        threshold,
        2 * best_tp / best_denominator,
        len(selected),
        positives,
        candidates,
        digest.hexdigest(),
    )
