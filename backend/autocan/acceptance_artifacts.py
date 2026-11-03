"""Integrity and numeric comparison primitives for read-only M9 acceptance."""

import math
from hashlib import sha256
from pathlib import Path
from typing import Any

from autocan.factory import _read_json
from autocan.parquet import MAX_BUNDLE_MANIFEST_BYTES, _file_hash
from autocan.traces import TraceError
from autocan.training_artifacts import _path
from autocan.validation import integer, object_value
from autocan.window_benchmark import MAX_OUTPUT_BYTES

ABSOLUTE_TOLERANCE = 1e-12


def checksum(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise TraceError("invalid acceptance SHA-256")
    return value


def compare_scientific(actual: Any, expected: Any, path: str = "metrics") -> None:
    """Exact counts/structure/nulls; finite floating values permit absolute 1e-12."""
    if type(actual) is bool or type(expected) is bool or actual is None or expected is None:
        equal = type(actual) is type(expected) and actual == expected
    elif isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        equal = (
            type(actual) is type(expected) and actual == expected
            if type(actual) is int or type(expected) is int
            else math.isfinite(actual)
            and math.isfinite(expected)
            and abs(actual - expected) <= ABSOLUTE_TOLERANCE
        )
    elif isinstance(actual, dict) and isinstance(expected, dict):
        if actual.keys() != expected.keys():
            raise TraceError(f"acceptance mismatch at {path}: object fields")
        for name in expected:
            compare_scientific(actual[name], expected[name], f"{path}.{name}")
        return
    elif isinstance(actual, list) and isinstance(expected, list):
        if len(actual) != len(expected):
            raise TraceError(f"acceptance mismatch at {path}: row coverage")
        for index, (a, b) in enumerate(zip(actual, expected, strict=True)):
            compare_scientific(a, b, f"{path}[{index}]")
        return
    else:
        equal = type(actual) is type(expected) and actual == expected
    if not equal:
        raise TraceError(f"acceptance mismatch at {path}")


def published(root: Path, format_name: str, names: set[str]) -> dict[str, Any]:
    meta = _read_json(_path(root, "manifest.json"), MAX_BUNDLE_MANIFEST_BYTES)
    if (
        meta.get("format") != format_name
        or integer(meta.get("format_version"), "evidence format") != 1
        or meta.get("status") != "complete"
        or meta.get("models_trained") != []
        or meta.get("thresholds_selected") != []
    ):
        raise TraceError("unsupported or non-frozen acceptance evidence")
    checksum(meta.get("implementation_sha256"))
    inventory = object_value(meta.get("artifacts"), "evidence inventory")
    if inventory.keys() != names:
        raise TraceError("unsupported acceptance evidence inventory")
    used = _path(root, "manifest.json").stat().st_size
    for name, raw in inventory.items():
        info = object_value(raw, "evidence artifact")
        used += integer(info.get("bytes"), "evidence bytes")
        if used > MAX_OUTPUT_BYTES:
            raise TraceError("acceptance evidence exceeds 64 MiB")
        path = _path(root, name)
        if path.stat().st_size != info["bytes"] or _file_hash(path) != checksum(info.get("sha256")):
            raise TraceError(f"acceptance evidence checksum/size mismatch: {name}")
    return meta


def fingerprints(roots: dict[str, Path]) -> dict[str, Any]:
    return {
        role: {
            p.relative_to(root).as_posix(): _file_hash(_path(root, p.relative_to(root).as_posix()))
            for p in sorted(root.rglob("*"))
            if p.is_file()
        }
        for role, root in roots.items()
    }


def digest_json(value: object) -> str:
    from autocan.validation import canonical_bytes

    return sha256(canonical_bytes(value)).hexdigest()
