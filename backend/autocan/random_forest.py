"""Supervised binary RandomForest using only eligible labeled train windows."""

from array import array
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha256

import numpy as np
from sklearn.ensemble import RandomForestClassifier

from autocan.preprocessing import MAX_FIT_VALUES, Preprocessor
from autocan.training_artifacts import PreparedWindow
from autocan.validation import canonical_bytes, integer, number

MODEL_ID = "random_forest"
MODEL_VERSION = "random-forest-labeled-train-1"


@dataclass(frozen=True, slots=True)
class FittedRandomForest:
    estimator: RandomForestClassifier
    preprocessing: Preprocessor
    fit_rows: int
    fit_rows_sha256: str
    seed: int
    fit_positive_windows: int
    label_rows_sha256: str

    def score(self, rows: Sequence[PreparedWindow]) -> list[float]:
        if not rows:
            return []
        matrix = np.asarray(
            [
                self.preprocessing.transform(row.features, columns=self.preprocessing.columns)
                for row in rows
            ],
            dtype=np.float64,
        )
        positive = list(self.estimator.classes_).index(1)
        probabilities = self.estimator.predict_proba(matrix)[:, positive]
        return [
            number(float(value), "anomaly probability", minimum=0, maximum=1)
            for value in probabilities
        ]


def fit_random_forest(
    rows: Iterable[PreparedWindow],
    state: Preprocessor,
    *,
    seed: int = 42,
    max_values: int = MAX_FIT_VALUES,
) -> FittedRandomForest:
    if integer(seed, "model seed") > 2**32 - 1:
        raise ValueError("model seed must fit an unsigned 32-bit integer")
    if state.normal_only:
        raise ValueError("RandomForest requires frozen all-train preprocessing")
    if integer(max_values, "model fit quota", minimum=1) > MAX_FIT_VALUES:
        raise ValueError("model fit quota exceeds supported limit")
    values, labels = array("d"), array("b")
    indices: set[int] = set()
    digest, targets_digest = sha256(), sha256()
    positives = 0
    for row in rows:
        if row.key.split != "train" or not row.key.eligible:
            continue
        if type(row.label) is not bool or row.key.row_index in indices:
            raise ValueError("invalid label or duplicate supervised train window")
        if len(values) + len(state.columns) > max_values:
            raise ValueError("model fit value quota exceeded")
        indices.add(row.key.row_index)
        digest.update(canonical_bytes(row.key.row_index) + b"\n")
        targets_digest.update(
            canonical_bytes(
                {
                    "row_index": row.key.row_index,
                    "label": row.label,
                }
            )
            + b"\n"
        )
        values.extend(state.transform(row.features, columns=state.columns))
        labels.append(int(row.label))
        positives += int(row.label)
    if not 0 < positives < len(indices):
        raise ValueError("RandomForest requires both train label classes")
    if len(indices) != state.fit_rows or digest.hexdigest() != state.fit_rows_sha256:
        raise ValueError("model/preprocessing fit scope fingerprint mismatch")
    estimator = RandomForestClassifier(
        n_estimators=100,
        criterion="gini",
        max_depth=12,
        min_samples_split=2,
        min_samples_leaf=2,
        max_features="sqrt",
        max_leaf_nodes=256,
        bootstrap=True,
        oob_score=False,
        class_weight="balanced",
        max_samples=None,
        n_jobs=1,
        random_state=seed,
        verbose=0,
        warm_start=False,
    )
    matrix = np.frombuffer(values, dtype=np.float64).reshape(len(indices), len(state.columns))
    targets = np.frombuffer(labels, dtype=np.int8)
    estimator.fit(matrix, targets)
    return FittedRandomForest(
        estimator,
        state,
        len(indices),
        digest.hexdigest(),
        seed,
        positives,
        targets_digest.hexdigest(),
    )
