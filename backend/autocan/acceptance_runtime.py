"""Validate historical measurements without rerunning inference or comparing machines."""

from itertools import product
from pathlib import Path
from typing import Any

from autocan.acceptance_artifacts import checksum, compare_scientific, published
from autocan.evaluation import RULE_IDS
from autocan.factory import _read_json
from autocan.inference_benchmark import (
    BATCH_SIZES,
    BENCHMARK_VERSION,
    MODES,
    PARITY_ATOL,
    THREAD_VARIABLES,
    BenchmarkConfig,
    summarize,
)
from autocan.model_artifacts import MODEL_IDS
from autocan.parquet import _file_hash
from autocan.traces import TraceError
from autocan.validation import integer, number
from autocan.window_benchmark import MAX_OUTPUT_BYTES


def verify_runtime(
    root: Path,
    prepared: dict[str, Any],
    experiment: dict[str, Any],
) -> dict[str, Any]:
    meta = published(root, "autocan-inference-benchmark-manifest", {"benchmark.json", "report.md"})
    data = _read_json(root / "benchmark.json", MAX_OUTPUT_BYTES)
    if (
        data.get("format") != "autocan-frozen-inference-benchmark"
        or data.get("format_version") != 1
        or data.get("benchmark_version") != BENCHMARK_VERSION
        or meta.get("benchmark_version") != BENCHMARK_VERSION
        or meta.get("benchmark_sha256") != _file_hash(root / "benchmark.json")
    ):
        raise TraceError("invalid inference evidence format/hash")
    # Schema validation errors are CLI validation failures, not partial acceptance.
    try:
        return _verify(data, meta, prepared, experiment)
    except (KeyError, TypeError, AttributeError) as exc:
        raise TraceError("invalid inference evidence schema") from exc


def _verify(
    data: dict[str, Any],
    meta: dict[str, Any],
    prepared: dict[str, Any],
    experiment: dict[str, Any],
) -> dict[str, Any]:
    configuration = data["configuration"]
    config = BenchmarkConfig(
        **{
            name: configuration[name]
            for name in (
                "windows",
                "warmups",
                "repetitions",
                "threads",
            )
        },
        worker_timeout_seconds=configuration.get("worker_timeout_seconds"),
    )
    detectors = (*RULE_IDS, *MODEL_IDS)
    if (
        configuration["batch_sizes"] != list(BATCH_SIZES)
        or configuration["modes"] != list(MODES)
        or configuration["detector_order"] != list(detectors)
        or integer(configuration["pythonhashseed"], "hash seed") != 0
    ):
        raise TraceError("inference evidence measurement policy mismatch")
    workload = data["workload"]
    if (
        workload["windows"] != config.windows
        or workload["ordering"] != "first N eligible ID windows in saved row order; no shuffle"
        or workload["prepared_semantic_sha256"] != prepared["semantic_sha256"]
        or config.windows > prepared["artifacts"]["features.parquet"]["records"]
    ):
        raise TraceError("inference evidence dataset/workload mismatch")
    checksum(workload["ordered_keys_sha256"])
    checksum(workload["experiment_semantic_sha256"])
    inputs = meta["inputs"]
    if set(inputs) != {"source", "prepared", "experiment"}:
        raise TraceError("invalid inference input roster")
    for inventory in inputs.values():
        checksum(inventory["manifest.json"])
        for name, value in inventory.items():
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise TraceError("unsafe inference fingerprint path")
            checksum(value)
    bound = [
        f"models/{model}/{file}"
        for model in MODEL_IDS
        for file in ("model.pkl", "preprocessing.json", "threshold.json")
    ]
    for name in [*bound, "scores.parquet"]:
        if inputs["experiment"][name] != experiment["artifacts"][name]["sha256"]:
            raise TraceError(f"inference evidence frozen model/score mismatch: {name}")
    for name in (
        "features.parquet",
        "labels/windows.parquet",
        "scaled/all_train.parquet",
        "scaled/normal_train.parquet",
    ):
        if inputs["prepared"][name] != prepared["artifacts"][name]["sha256"]:
            raise TraceError(f"inference evidence numeric dataset mismatch: {name}")
    differences = [
        name
        for name in [*(f"models/{m}/model.json" for m in MODEL_IDS), "split.json"]
        if inputs["experiment"][name] != experiment["artifacts"][name]["sha256"]
    ]
    environment = data["environment"]
    if (
        environment["system"] not in ("Darwin", "Linux")
        or type(environment["ci"]) is not bool
        or environment["package_versions"] != experiment["package_versions"]
    ):
        raise TraceError("inference evidence environment mismatch")
    integer(environment["hardware"]["logical_cpus"], "logical CPUs", minimum=1)
    expected = set(product(detectors, BATCH_SIZES, MODES))
    seen: set[tuple[str, int, str]] = set()
    tokens: set[str] = set()
    measurements = []
    for row in data["rows"]:
        key = row["detector"], row["batch_size"], row["mode"]
        if key not in expected or key in seen:
            raise TraceError("inference evidence detector/batch/scope coverage mismatch")
        seen.add(key)
        if key[0] in RULE_IDS and key[2] == "estimator_only":
            if row["status"] != "not_applicable":
                raise TraceError("rules have no estimator-only measurement")
            continue
        if row["status"] != "measured":
            raise TraceError("missing inference measurement")
        batch = integer(row["batch_size"], "measured batch", minimum=1)
        durations = row["batch_durations_ns"]
        if len(durations) != config.windows // batch * config.repetitions:
            raise TraceError("inference evidence repetition/sample count mismatch")
        compare_scientific(row["metrics"], summarize(durations, batch), "inference.metrics")
        elapsed = row["run_elapsed_ns"]
        per_run = config.windows // batch
        if len(elapsed) != config.repetitions or any(
            integer(n, "run wall time", minimum=1) < sum(durations[i * per_run : (i + 1) * per_run])
            for i, n in enumerate(elapsed)
        ):
            raise TraceError("invalid inference per-run wall times")
        parity = row["parity"]
        if (
            parity["windows"] != config.windows
            or parity["absolute_tolerance"] != PARITY_ATOL
            or parity["relative_tolerance"] != 0
        ):
            raise TraceError("invalid frozen score parity policy")
        number(parity["max_absolute_error"], "saved parity error", minimum=0, maximum=PARITY_ATOL)
        integer(row["peak_process_rss_bytes"], "normalized peak RSS", minimum=1)
        integer(row["worker_pid"], "worker PID", minimum=1)
        token = row["worker_token"]
        if not isinstance(token, str) or len(token) != 32 or token in tokens:
            raise TraceError("inference workers are not distinct")
        tokens.add(token)
        if row["thread_environment"] != {name: "1" for name in THREAD_VARIABLES} or any(
            integer(p["num_threads"], "native threads") != 1 for p in row["threadpools"]
        ):
            raise TraceError("inference evidence thread policy mismatch")
        measurements.append(
            {
                k: row[k]
                for k in (
                    "detector",
                    "batch_size",
                    "mode",
                    "metrics",
                    "peak_process_rss_bytes",
                    "parity",
                )
            }
        )
    if seen != expected:
        raise TraceError("incomplete inference evidence")
    return {
        "local_m1_observation": (
            environment["system"] == "Darwin"
            and not environment["ci"]
            and environment["hardware"].get("chip_type") == "Apple M1"
        ),
        "environment": environment,
        "configuration": configuration,
        "workload": workload,
        "historical_implementation_sha256": meta["implementation_sha256"],
        "historical_git_commit": meta.get("git_commit"),
        "historical_git_dirty": meta.get("git_dirty"),
        "measurements": measurements,
        "identity_bridge": {
            "model_preprocessing_thresholds_match": True,
            "numeric_features_labels_scaled_and_scores_match": True,
            "different_metadata": differences,
            "quality_experiment_semantic_sha256": experiment["semantic_sha256"],
            "capture_experiment_semantic_sha256": workload["experiment_semantic_sha256"],
            "policy": (
                "content equivalence; separate experiment/split/run identities remain distinct"
            ),
        },
        "verification": (
            "historical artifact integrity and metric arithmetic; no new timing or scoring"
        ),
    }
