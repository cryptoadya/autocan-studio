"""Held-out fault types and normal families, bound to a completed ID benchmark."""

from dataclasses import asdict
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from typing import TypedDict

from autocan.benchmark import MAX_BENCHMARK_BYTES
from autocan.evaluation import EVALUATOR_VERSION, validate_split
from autocan.factory import (
    DEFAULT_FACTORY_BYTES,
    DRIVING_MODES,
    MAX_FACTORY_BYTES,
    RUN_FILES,
    FactoryConfig,
    FactoryManifest,
    _read_json,
    _safe_relative,
    generate_factory,
    verify_factory,
)
from autocan.faults import FAULT_TYPES
from autocan.features import DETECTOR_ID, FEATURE_SCHEMA_VERSION, FeatureConfig
from autocan.parquet import _file_hash, _new_output
from autocan.traces import TraceError
from autocan.validation import canonical_bytes, fields, integer, number, object_value

OOD_VERSION = "m8-heldout-1"
DOMAINS = ("ood_fault", "ood_normal")
REFERENCE_FILES = {"split.json", "runs.jsonl", "metrics.json", "report.md"}
MAX_OOD_MANIFEST_BYTES = 4 * 1024 * 1024


class FrozenReference(TypedDict):
    source_manifest_sha256: str
    benchmark_manifest_sha256: str
    dataset_semantic_sha256: str
    feature_configuration: dict[str, object]


class OODProtocol(TypedDict):
    format: str
    format_version: int
    generator_version: str
    reference: FrozenReference
    feature_configuration: dict[str, object]
    seed: int
    fault_runs: int
    normal_runs: int
    max_bytes: int
    known_modes: list[str]
    known_fault_types: list[str]
    ood_fault_configuration: dict[str, object]
    ood_normal_configuration: dict[str, object]


def feature_configuration(value: object) -> FeatureConfig:
    data = object_value(value, "frozen feature configuration")
    fields(data, set(asdict(FeatureConfig())))
    return FeatureConfig(
        window_ns=integer(data["window_ns"], "window", minimum=1),
        heartbeat_periods=integer(data["heartbeat_periods"], "heartbeat periods", minimum=1),
        rpm_derivative_threshold=number(
            data["rpm_derivative_threshold"], "RPM threshold", minimum=0, maximum=1e12
        ),
        wheel_residual_threshold=number(
            data["wheel_residual_threshold"], "wheel threshold", minimum=0, maximum=360
        ),
    )


def _verify_inventory(root: Path, meta: dict[str, object], expected: set[str], limit: int) -> None:
    inventory = object_value(meta.get("artifacts"), "artifacts")
    if set(inventory) != expected:
        raise TraceError("unsupported artifact inventory")
    used = _safe_relative(root, "manifest.json").stat().st_size
    for name, value in inventory.items():
        info = object_value(value, "artifact")
        fields(info, {"bytes", "sha256"})
        size = integer(info["bytes"], "artifact size")
        used += size
        if used > limit:
            raise TraceError("OOD/reference artifact quota exceeded")
        path = _safe_relative(root, name)
        if path.stat().st_size != size or _file_hash(path) != info["sha256"]:
            raise TraceError(f"artifact checksum or size mismatch: {name}")


def verify_reference(source: Path, reference: Path) -> tuple[FactoryManifest, FrozenReference]:
    """Validate ID provenance, split, configuration and archived benchmark hashes."""
    meta = verify_factory(source)
    saved = _read_json(_safe_relative(reference, "manifest.json"))
    if any(
        saved.get(key) != value
        for key, value in {
            "format": "autocan-tier0-benchmark",
            "format_version": 1,
            "status": "complete",
            "evaluator_version": EVALUATOR_VERSION,
            "detector_id": DETECTOR_ID,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "dataset_semantic_sha256": meta["semantic_sha256"],
            "source_manifest_sha256": _file_hash(source / "manifest.json"),
        }.items()
    ):
        raise TraceError("incompatible ID benchmark reference")
    implementation = sha256()
    for name in ("features", "evaluation", "benchmark"):
        implementation.update(
            name.encode() + b"\0" + files("autocan").joinpath(f"{name}.py").read_bytes()
        )
    if saved.get("implementation_sha256") != implementation.hexdigest():
        raise TraceError("ID reference implementation is incompatible")
    _verify_inventory(reference, saved, REFERENCE_FILES, MAX_BENCHMARK_BYTES)
    validate_split(meta, _read_json(reference / "split.json"))
    config = asdict(feature_configuration(saved.get("configuration")))
    metrics = _read_json(reference / "metrics.json")
    if (
        metrics.get("configuration") != config
        or metrics.get("dataset_semantic_sha256") != meta["semantic_sha256"]
    ):
        raise TraceError("ID reference metrics/configuration mismatch")
    return meta, {
        "source_manifest_sha256": _file_hash(source / "manifest.json"),
        "benchmark_manifest_sha256": _file_hash(reference / "manifest.json"),
        "dataset_semantic_sha256": meta["semantic_sha256"],
        "feature_configuration": config,
    }


def plan_ood(
    meta: FactoryManifest,
    frozen: FrozenReference,
    *,
    seed: int,
    fault_runs: int,
    normal_runs: int,
    max_bytes: int = DEFAULT_FACTORY_BYTES,
) -> OODProtocol:
    if integer(seed, "OOD seed") > 2**63 - 1:
        raise ValueError("OOD seed must fit a nonnegative signed 64-bit integer")
    if not 8192 <= integer(max_bytes, "OOD quota") <= MAX_FACTORY_BYTES:
        raise ValueError("OOD quota must be in [8192 bytes, 1 GiB]")
    known_modes = [mode for mode in DRIVING_MODES if any(r["mode"] == mode for r in meta["runs"])]
    known_faults = [
        kind for kind in FAULT_TYPES if any(r["fault_type"] == kind for r in meta["runs"])
    ]
    unseen_faults = tuple(kind for kind in FAULT_TYPES if kind not in known_faults)
    unseen_modes = tuple(mode for mode in DRIVING_MODES if mode not in known_modes)
    if not unseen_faults or not unseen_modes:
        raise ValueError(
            "OOD requires reserved fault types and normal mode families absent from ID"
        )
    original = FactoryConfig.from_dict(meta["configuration"])

    def configuration(domain: str, runs: int) -> dict[str, object]:
        stream_seed = int.from_bytes(
            sha256(canonical_bytes([OOD_VERSION, seed, domain])).digest()[:8], "big"
        ) & (2**63 - 1)
        return FactoryConfig(
            runs=runs,
            seed=stream_seed,
            modes=tuple(known_modes) if domain == "ood_fault" else unseen_modes,
            fault_runs=runs if domain == "ood_fault" else 0,
            fault_types=unseen_faults,
            receiver_id=original.receiver_id,
            min_duration_ns=original.min_duration_ns,
            max_duration_ns=original.max_duration_ns,
            max_bytes=max_bytes // 2,
        ).as_dict()

    return {
        "format": "autocan-ood-protocol",
        "format_version": 1,
        "generator_version": OOD_VERSION,
        "reference": frozen,
        "feature_configuration": frozen["feature_configuration"],
        "seed": seed,
        "fault_runs": fault_runs,
        "normal_runs": normal_runs,
        "max_bytes": max_bytes,
        "known_modes": known_modes,
        "known_fault_types": known_faults,
        "ood_fault_configuration": configuration("ood_fault", fault_runs),
        "ood_normal_configuration": configuration("ood_normal", normal_runs),
    }


def check_isolation(collections: dict[str, FactoryManifest]) -> None:
    """Reject identities or exact scenario/input duplicates across domains."""
    for key in ("run_id", "scenario_sha256", "observation_semantic_sha256"):
        seen: set[str] = set()
        for name, meta in collections.items():
            values = {str(row[key]) for row in meta["runs"]}
            if values & seen or (key == "run_id" and len(values) != len(meta["runs"])):
                raise ValueError(f"OOD domain overlap in {key}: {name}")
            seen.update(values)


def _suite_identity(protocol: OODProtocol, collections: dict[str, FactoryManifest]) -> str:
    return sha256(
        canonical_bytes(
            {
                "protocol": protocol,
                "collections": {
                    name: meta["semantic_sha256"]
                    for name, meta in collections.items()
                    if name != "id"
                },
            }
        )
    ).hexdigest()


def generate_ood(
    source: Path,
    reference: Path,
    target: Path,
    *,
    seed: int = 43,
    fault_runs: int = 6,
    normal_runs: int = 6,
    max_bytes: int = DEFAULT_FACTORY_BYTES,
) -> dict[str, object]:
    meta, frozen = verify_reference(source, reference)
    protocol = plan_ood(
        meta, frozen, seed=seed, fault_runs=fault_runs, normal_runs=normal_runs, max_bytes=max_bytes
    )
    with _new_output(target) as staging:
        # Freeze the full protocol before generating or observing any OOD run.
        (staging / "protocol.json").write_bytes(canonical_bytes(protocol) + b"\n")
        collections = {"id": meta}
        for domain, config in zip(
            DOMAINS,
            (protocol["ood_fault_configuration"], protocol["ood_normal_configuration"]),
            strict=True,
        ):
            collections[domain] = generate_factory(
                FactoryConfig.from_dict(config), staging / domain
            )
        check_isolation(collections)
        inventory = {
            path.relative_to(staging).as_posix(): {
                "bytes": path.stat().st_size,
                "sha256": _file_hash(path),
            }
            for path in sorted(staging.rglob("*"))
            if path.is_file()
        }
        manifest: dict[str, object] = {
            "format": "autocan-ood-suite",
            "format_version": 1,
            "status": "complete",
            "generator_version": OOD_VERSION,
            "artifacts": inventory,
            "semantic_sha256": _suite_identity(protocol, collections),
        }
        raw = canonical_bytes(manifest) + b"\n"
        if len(raw) > MAX_OOD_MANIFEST_BYTES:
            raise TraceError("OOD manifest quota exceeded")
        if (
            sum(path.stat().st_size for path in staging.rglob("*") if path.is_file()) + len(raw)
            > max_bytes
        ):
            raise TraceError("OOD suite quota exceeded")
        (staging / "manifest.json").write_bytes(raw)
    return manifest


def verify_ood(
    source: Path,
    reference: Path,
    suite: Path,
) -> tuple[OODProtocol, dict[str, FactoryManifest]]:
    try:
        meta, frozen = verify_reference(source, reference)
        data = _read_json(_safe_relative(suite, "protocol.json"))
        protocol = plan_ood(
            meta,
            frozen,
            seed=integer(data.get("seed"), "OOD seed"),
            fault_runs=integer(data.get("fault_runs"), "OOD fault runs", minimum=1),
            normal_runs=integer(data.get("normal_runs"), "OOD normal runs", minimum=1),
            max_bytes=integer(data.get("max_bytes"), "OOD quota"),
        )
        if data != protocol:
            raise TraceError("frozen OOD protocol/reference mismatch")
        saved = _read_json(_safe_relative(suite, "manifest.json"), MAX_OOD_MANIFEST_BYTES)
        fields(
            saved,
            {
                "format",
                "format_version",
                "status",
                "generator_version",
                "artifacts",
                "semantic_sha256",
            },
        )
        if any(
            saved[key] != value
            for key, value in {
                "format": "autocan-ood-suite",
                "format_version": 1,
                "status": "complete",
                "generator_version": OOD_VERSION,
            }.items()
        ):
            raise TraceError("unsupported OOD suite")
        expected = {"protocol.json"}
        configs = (protocol["ood_fault_configuration"], protocol["ood_normal_configuration"])
        for domain, config in zip(DOMAINS, configs, strict=True):
            expected.add(f"{domain}/manifest.json")
            expected.update(
                f"{domain}/runs/{i:04d}/{name}"
                for i in range(integer(config["runs"], "runs"))
                for name in RUN_FILES
            )
        _verify_inventory(suite, saved, expected, protocol["max_bytes"])
        collections = {"id": meta}
        for domain, config in zip(DOMAINS, configs, strict=True):
            actual = collections[domain] = verify_factory(suite / domain)
            if actual["configuration"] != config:
                raise TraceError("OOD collection configuration mismatch")
        check_isolation(collections)
        if saved["semantic_sha256"] != _suite_identity(protocol, collections):
            raise TraceError("OOD suite semantic checksum mismatch")
        return protocol, collections
    except (KeyError, OSError, TypeError) as exc:
        raise TraceError(str(exc)) from exc
