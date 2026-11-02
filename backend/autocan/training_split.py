"""Stratified whole-run groups for preparation, preserving the M8 split contract."""

from hashlib import sha256
from random import Random
from typing import TypedDict

from autocan.evaluation import SPLITS, Assignment, make_split
from autocan.factory import FactoryManifest
from autocan.validation import canonical_bytes, integer, object_value

TRAINING_SPLIT_VERSION = "mode-fault-groups-1"


class TrainingSplit(TypedDict):
    format: str
    format_version: int
    algorithm: str
    dataset_semantic_sha256: str
    seed: int
    assignments: list[Assignment]
    strata: list[dict[str, object]]


def make_training_split(meta: FactoryManifest, *, seed: int) -> TrainingSplit:
    grouped = make_split(meta, seed=seed)  # reuse exact-duplicate grouping, not its memberships
    rows = {row["index"]: row for row in meta["runs"]}
    groups: dict[int, list[int]] = {}
    for assignment in grouped["assignments"]:
        groups.setdefault(assignment["group"], []).append(assignment["index"])
    strata: dict[tuple[str, str], list[int]] = {}
    for group, indices in groups.items():
        kinds = {(rows[i]["mode"], rows[i]["fault_type"] or "normal") for i in indices}
        if len(kinds) != 1:
            raise ValueError(
                "coverage cannot split a duplicate input group across mode/fault strata"
            )
        strata.setdefault(next(iter(kinds)), []).append(group)
    modes = meta["configuration"].get("modes")
    kinds_config = meta["configuration"].get("fault_types")
    if not isinstance(modes, list) or not isinstance(kinds_config, list):
        raise ValueError("coverage requires configured modes and fault types")
    expected = {(str(mode), str(kind)) for mode in modes for kind in ["normal", *kinds_config]}
    if set(strata) != expected or any(len(groups) < 3 for groups in strata.values()):
        raise ValueError(
            "coverage requires >=3 independent groups per configured mode/normal/fault stratum"
        )
    membership: dict[int, str] = {}
    coverage: list[dict[str, object]] = []
    for (mode, kind), selected in sorted(strata.items()):
        selected = sorted(selected)
        stream = sha256(canonical_bytes([TRAINING_SPLIT_VERSION, seed, mode, kind])).digest()
        Random(int.from_bytes(stream[:8], "big")).shuffle(selected)
        held = max(1, len(selected) // 5)
        for i, group in enumerate(selected):
            membership[group] = "test" if i < held else "validation" if i < 2 * held else "train"
        coverage.append(
            {
                "mode": mode,
                "fault_type": None if kind == "normal" else kind,
                "groups": len(selected),
                "partitions": {
                    part: {
                        "groups": sum(membership[g] == part for g in selected),
                        "runs": sum(len(groups[g]) for g in selected if membership[g] == part),
                    }
                    for part in SPLITS
                },
            }
        )
    assignments: list[Assignment] = [
        {**row, "split": membership[row["group"]]} for row in grouped["assignments"]
    ]
    return {
        "format": "autocan-training-split",
        "format_version": 1,
        "algorithm": TRAINING_SPLIT_VERSION,
        "dataset_semantic_sha256": meta["semantic_sha256"],
        "seed": seed,
        "assignments": assignments,
        "strata": coverage,
    }


def validate_training_split(meta: FactoryManifest, value: object) -> TrainingSplit:
    data = object_value(value, "training split")
    expected = make_training_split(meta, seed=integer(data.get("seed"), "split seed"))
    if data != expected:
        raise ValueError("training split is overlapping, incomplete or incompatible")
    return expected
