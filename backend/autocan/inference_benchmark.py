"""Wall-clock measurements of trusted frozen M9 scoring, in fresh processes.

No feature extraction, fitting, selection, or alert/event evaluation occurs here.
"""

import json
import math
import os
import platform
import resource
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import partial
from hashlib import sha256
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from time import perf_counter_ns
from typing import Any
from uuid import uuid4

import numpy as np
import pyarrow as pa
from threadpoolctl import threadpool_info, threadpool_limits  # type: ignore[import-untyped]

from autocan.evaluation import RULE_IDS
from autocan.model_artifacts import (
    MODEL_IDS,
    FrozenExperimentMetadata,
    inspect_frozen_experiment,
    load_frozen_experiment,
)
from autocan.parquet import _file_hash, _new_output
from autocan.score_artifacts import iter_saved_scores, saved_policies
from autocan.traces import TraceError, _git_provenance, _packages
from autocan.training_artifacts import LoadedTrainingData, PreparedWindow, load_training_data
from autocan.validation import canonical_bytes, integer
from autocan.window_evaluation import baseline_window_scores

BENCHMARK_VERSION = "frozen-inference-1"
BATCH_SIZES = (1, 256)
MODES = ("preprocessing_scoring", "estimator_only")
THREAD_VARIABLES = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "BLIS_NUM_THREADS",
    "OMP_THREAD_LIMIT",
    "GOTO_NUM_THREADS",
)
PARITY_ATOL = 1e-12


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    windows: int = 256
    warmups: int = 2
    repetitions: int = 5
    threads: int = 1
    worker_timeout_seconds: int | None = None

    def __post_init__(self) -> None:
        for name, low, high in (
            ("windows", 256, 4096),
            ("warmups", 1, 100),
            ("repetitions", 2, 100),
            ("threads", 1, 1),
        ):
            value = integer(getattr(self, name), name, minimum=low)
            if value > high:
                raise ValueError(f"{name} exceeds supported limit {high}")
        if self.worker_timeout_seconds is not None:
            integer(self.worker_timeout_seconds, "worker timeout seconds", minimum=1)
        if self.windows % 256:
            raise ValueError("windows must be a multiple of 256 (no partial batches)")


def rss_bytes(value: int, system: str) -> int:
    """getrusage ru_maxrss is bytes on Darwin and KiB on Linux."""
    if system not in ("Darwin", "Linux"):
        raise ValueError("RSS measurement supports Darwin and Linux only")
    return value * (1 if system == "Darwin" else 1024)


def summarize(durations_ns: Sequence[int], batch_size: int) -> dict[str, float]:
    integer(batch_size, "batch size", minimum=1)
    if not durations_ns or any(type(n) is not int or n <= 0 for n in durations_ns):
        raise ValueError("measurement durations must be positive integer nanoseconds")
    ordered = sorted(durations_ns)

    def percentile(q: float) -> float:
        position = (len(ordered) - 1) * q
        low = math.floor(position)
        high = math.ceil(position)
        return (ordered[low] + (ordered[high] - ordered[low]) * (position - low)) / 1e6

    return {
        "batch_latency_ms_p50": percentile(0.5),
        "batch_latency_ms_p95": percentile(0.95),
        "windows_per_second": len(ordered) * batch_size * 1e9 / sum(ordered),
    }


def check_parity(actual: Sequence[float], expected: Sequence[float]) -> float:
    if len(actual) != len(expected) or not actual:
        raise TraceError("score parity coverage mismatch")
    errors = []
    for a, b in zip(actual, expected, strict=True):
        if not math.isfinite(a) or not math.isfinite(b) or abs(a - b) > PARITY_ATOL:
            raise TraceError("frozen score parity mismatch (absolute tolerance 1e-12)")
        errors.append(abs(a - b))
    return max(errors)


def input_fingerprints(prepared: Path, source: Path, experiment: Path) -> dict[str, object]:
    # Preserve exact file identities as well as manifest lineage. No source is written.
    return {
        name: {
            p.relative_to(root).as_posix(): _file_hash(p)
            for p in sorted(root.rglob("*"))
            if p.is_file()
        }
        for name, root in (("prepared", prepared), ("source", source), ("experiment", experiment))
    }


def _load_workload(
    prepared: Path,
    source: Path,
    experiment: Path,
    config: BenchmarkConfig,
) -> tuple[
    LoadedTrainingData, FrozenExperimentMetadata, list[PreparedWindow], dict[str, list[float]]
]:
    data = load_training_data(prepared, source=source)
    frozen = inspect_frozen_experiment(experiment, data)
    # Exhaust both streams: suffix corruption/coverage cannot hide behind a prefix workload.
    windows = list(data.iter_windows())
    saved = list(
        iter_saved_scores(
            experiment,
            frozen.manifest,
            (w.key for w in windows),
            saved_policies(data, frozen),
        )
    )
    indices = [i for i, w in enumerate(windows) if w.key.eligible][: config.windows]
    if len(indices) != config.windows:
        raise TraceError("not enough eligible saved windows for the requested fixed workload")
    return (
        data,
        frozen,
        [windows[i] for i in indices],
        {name: [saved[i][name] for i in indices] for name in (*RULE_IDS, *MODEL_IDS)},
    )


def measure_worker(
    prepared: Path,
    source: Path,
    experiment: Path,
    detector: str,
    batch_size: int,
    mode: str,
    config: BenchmarkConfig,
) -> dict[str, Any]:
    if (
        detector not in (*RULE_IDS, *MODEL_IDS)
        or batch_size not in BATCH_SIZES
        or mode not in MODES
    ):
        raise ValueError("invalid benchmark worker detector/batch/mode")
    if detector in RULE_IDS and mode == "estimator_only":
        raise ValueError("rules have no separate estimator")
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    with threadpool_limits(limits=config.threads):
        data, _, windows, saved = _load_workload(prepared, source, experiment, config)
        batches = [windows[i : i + batch_size] for i in range(0, len(windows), batch_size)]
        calls: list[Callable[[], Sequence[float]]] = []
        if detector in RULE_IDS:

            def score_rule(batch: list[PreparedWindow]) -> list[float]:
                return [
                    baseline_window_scores(
                        dict(zip(data.columns, row.features, strict=True)),
                        data.configuration,
                    )[detector].score
                    for row in batch
                ]

            calls = [partial(score_rule, batch) for batch in batches]
        else:
            # Only this detector's pickle is deserialized; sibling trees never enter this process.
            model = load_frozen_experiment(experiment, data, detector=detector).models[detector]
            if model.estimator.n_jobs != 1:
                raise TraceError("frozen estimator must already have n_jobs=1")
            if mode == "preprocessing_scoring":
                calls = [partial(model.score, batch) for batch in batches]
            else:
                matrices = [
                    np.asarray(
                        [
                            model.preprocessing.transform(row.features, columns=data.columns)
                            for row in batch
                        ],
                        dtype=np.float64,
                    )
                    for batch in batches
                ]
                if detector == "isolation_forest":

                    def score_estimator(matrix: Any) -> Any:
                        return -model.estimator.score_samples(matrix)

                    calls = [partial(score_estimator, matrix) for matrix in matrices]
                else:
                    positive = list(model.estimator.classes_).index(1)

                    def probability_estimator(matrix: Any) -> Any:
                        return model.estimator.predict_proba(matrix)[:, positive]

                    calls = [partial(probability_estimator, matrix) for matrix in matrices]

        # Parity is checked for this exact batch/mode before warm-up and after timing.
        def parity() -> float:
            return check_parity([float(v) for call in calls for v in call()], saved[detector])

        error = parity()
        for _ in range(config.warmups):
            for call in calls:
                call()
        durations: list[int] = []
        elapsed_runs = []
        for _ in range(config.repetitions):
            start_run = perf_counter_ns()
            for call in calls:
                start = perf_counter_ns()
                call()
                durations.append(perf_counter_ns() - start)
            elapsed_runs.append(perf_counter_ns() - start_run)
        error = max(error, parity())
        peak = rss_bytes(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, platform.system())
        pools = threadpool_info()
        if any(pool["num_threads"] != config.threads for pool in pools):
            raise TraceError("native threadpool limit was not applied")
        return {
            "detector": detector,
            "batch_size": batch_size,
            "mode": mode,
            "status": "measured",
            "worker_pid": os.getpid(),
            "worker_token": uuid4().hex,
            "metrics": summarize(durations, batch_size),
            "batch_durations_ns": durations,
            "run_elapsed_ns": elapsed_runs,
            "peak_process_rss_bytes": peak,
            "parity": {
                "windows": len(windows),
                "max_absolute_error": error,
                "absolute_tolerance": PARITY_ATOL,
                "relative_tolerance": 0,
            },
            "thread_environment": {name: os.environ.get(name) for name in THREAD_VARIABLES},
            "threadpools": pools,
        }


def _environment() -> dict[str, object]:
    hardware: dict[str, object] = {
        "logical_cpus": os.cpu_count(),
        "processor": platform.processor(),
        "machine": platform.machine(),
    }
    if platform.system() == "Darwin":
        probe = subprocess.run(
            ["/usr/sbin/system_profiler", "SPHardwareDataType", "-json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        info = json.loads(probe.stdout)["SPHardwareDataType"][0]
        # Deliberate allowlist: never record serial numbers, UUIDs or hostnames.
        hardware.update(
            {k: info[k] for k in ("chip_type", "machine_model", "physical_memory") if k in info}
        )
    elif platform.system() == "Linux":
        hardware["physical_memory_bytes"] = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        cpuinfo = Path("/proc/cpuinfo")
        if cpuinfo.exists():
            hardware["cpu_model"] = next(
                (
                    line.split(":", 1)[1].strip()
                    for line in cpuinfo.read_text().splitlines()
                    if line.startswith(("model name", "Hardware"))
                ),
                platform.processor(),
            )
    return {
        "hardware": hardware,
        "os": platform.platform(),
        "system": platform.system(),
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "package_versions": {
            **_packages(),
            **{
                name: version(name)
                for name in (
                    "pyarrow",
                    "numpy",
                    "scipy",
                    "scikit-learn",
                    "joblib",
                    "threadpoolctl",
                )
            },
        },
        "ci": bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")),
        "clock": "time.perf_counter_ns",
        "rss_source": "resource.getrusage(RUSAGE_SELF).ru_maxrss",
    }


def _report(result: dict[str, Any]) -> str:
    lines = [
        "# Frozen inference performance",
        "",
        f"Environment: {result['environment']['os']}; Python {result['environment']['python']}; "
        f"CI: {result['environment']['ci']}.",
        "",
        "| Detector | Batch | Scope | p50 batch ms | p95 batch ms | Windows/s | Peak RSS MiB |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in result["rows"]:
        if row["status"] != "measured":
            lines.append(
                f"| {row['detector']} | {row['batch_size']} | {row['mode']} |"
                " n/a | n/a | n/a | n/a |"
            )
            continue
        m = row["metrics"]
        lines.append(
            f"| {row['detector']} | {row['batch_size']} | {row['mode']} | "
            f"{m['batch_latency_ms_p50']:.6g} | {m['batch_latency_ms_p95']:.6g} | "
            f"{m['windows_per_second']:.6g} | {row['peak_process_rss_bytes'] / 2**20:.3f} |"
        )
    lines.extend(["", *result["limitations"], ""])
    return "\n".join(lines)


def _run_worker(
    request: dict[str, Any],
    environment: dict[str, str],
    config: BenchmarkConfig,
) -> dict[str, Any]:
    try:
        worker = subprocess.run(
            [sys.executable, "-m", "autocan.inference_benchmark"],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            env=environment,
            timeout=config.worker_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise TraceError(
            f"benchmark worker timed out after {config.worker_timeout_seconds} seconds "
            f"(detector={request['detector']}, batch={request['batch_size']}, "
            f"mode={request['mode']}); increase --worker-timeout-seconds "
            "or omit it to use no deadline"
        ) from exc
    if worker.returncode:
        raise TraceError(f"benchmark worker failed: {worker.stderr[-4000:]}")
    return dict(json.loads(worker.stdout))


def benchmark_inference(
    prepared: Path,
    target: Path,
    *,
    source: Path,
    experiment: Path,
    config: BenchmarkConfig | None = None,
) -> dict[str, object]:
    config = config or BenchmarkConfig()
    rss_bytes(1, platform.system())  # Fail before starting workers on unsupported platforms.
    for root in (prepared, source, experiment):
        a, b = target.resolve(), root.resolve()
        if a == b or a in b.parents or b in a.parents:
            raise TraceError("benchmark output must not overlap an input directory")
    with _new_output(target) as staging:
        before = input_fingerprints(prepared, source, experiment)
        data, frozen, windows, _ = _load_workload(prepared, source, experiment, config)
        workload_hash = sha256(
            b"".join(canonical_bytes(asdict(w.key)) + b"\n" for w in windows)
        ).hexdigest()
        environment = _environment()
        rows = []
        child_env = {**os.environ, **{name: str(config.threads) for name in THREAD_VARIABLES}}
        child_env["PYTHONHASHSEED"] = "0"
        for detector in (*RULE_IDS, *MODEL_IDS):
            for batch in BATCH_SIZES:
                for mode in MODES:
                    if detector in RULE_IDS and mode == "estimator_only":
                        rows.append(
                            {
                                "detector": detector,
                                "batch_size": batch,
                                "mode": mode,
                                "status": "not_applicable",
                                "reason": "rule has no estimator",
                            }
                        )
                        continue
                    request = {
                        "prepared": str(prepared.resolve()),
                        "source": str(source.resolve()),
                        "experiment": str(experiment.resolve()),
                        "detector": detector,
                        "batch_size": batch,
                        "mode": mode,
                        "configuration": asdict(config),
                    }
                    rows.append(_run_worker(request, child_env, config))
        if input_fingerprints(prepared, source, experiment) != before:
            raise TraceError("benchmark input changed during measurement")
        result = {
            "format": "autocan-frozen-inference-benchmark",
            "format_version": 1,
            "benchmark_version": BENCHMARK_VERSION,
            "created_utc": datetime.now(UTC).isoformat(),
            "configuration": {
                **asdict(config),
                "batch_sizes": BATCH_SIZES,
                "modes": MODES,
                "detector_order": (*RULE_IDS, *MODEL_IDS),
                "pythonhashseed": 0,
            },
            "environment": environment,
            "workload": {
                "ordering": "first N eligible ID windows in saved row order; no shuffle",
                "windows": len(windows),
                "ordered_keys_sha256": workload_hash,
                "prepared_semantic_sha256": data.manifest["semantic_sha256"],
                "experiment_semantic_sha256": frozen.manifest["semantic_sha256"],
            },
            "rows": rows,
            "limitations": [
                (
                    "Timing is machine/session specific; workload and procedure are "
                    "reproducible, not "
                    "cross-machine timings."
                ),
                (
                    "No CAN ingestion, decoding, feature extraction, scheduling, "
                    "threshold decisions "
                    "or alert delivery is timed; this is not CAN-to-alert latency."
                ),
                (
                    "Latency percentiles use linear interpolation over repeated batch-call wall "
                    "times; batch 256 latency is not per-window latency."
                ),
                (
                    "Throughput is total scored windows / summed batch-call seconds; "
                    "run_elapsed_ns "
                    "also includes timer/loop bookkeeping."
                ),
                (
                    "Estimator-only excludes frozen transform/matrix construction and Python score "
                    "validation; it retains score sign/class extraction."
                ),
                (
                    "Rules use raw feature mapping plus the existing joint three-rule scorer; each "
                    "reported rule call includes that helper's other rule calculations. "
                    "Rules have no "
                    "separate estimator."
                ),
                (
                    "Peak RSS is fresh-worker process high-water memory through post-run parity; "
                    "it includes imports, input validation, selected model loading, warm-up and "
                    "measurement; not "
                    "incremental estimator allocation."
                ),
                (
                    "Each detector/batch/scope uses a separate sequential process; RSS is "
                    "normalized "
                    "to bytes (Darwin bytes, Linux KiB)."
                ),
                (
                    "Inputs are trusted local pickles. Hashes verify integrity, not "
                    "authenticity. No "
                    "fit, threshold tuning, test/OOD model selection or new datasets."
                ),
                (
                    "Warm-ups are complete untimed workload sweeps. Parity uses the exact "
                    "batch/scope "
                    "before warm-up and after measurement, outside all timing."
                ),
                (
                    "Fixed prefix workload and repeated cache-hot runs do not represent every "
                    "operating regime; OS scheduling, thermal state, power mode and "
                    "background load "
                    "remain uncontrolled."
                ),
            ],
        }
        raw = canonical_bytes(result) + b"\n"
        (staging / "benchmark.json").write_bytes(raw)
        (staging / "report.md").write_text(_report(result))
        implementation = sha256()
        for path in sorted(files("autocan").iterdir(), key=lambda p: p.name):
            if path.name.endswith(".py"):
                implementation.update(path.name.encode() + b"\0" + path.read_bytes())
        manifest: dict[str, object] = {
            "format": "autocan-inference-benchmark-manifest",
            "format_version": 1,
            "status": "complete",
            "benchmark_version": BENCHMARK_VERSION,
            "inputs": before,
            "models_trained": [],
            "thresholds_selected": [],
            "implementation_sha256": implementation.hexdigest(),
            **_git_provenance(),
            "benchmark_sha256": sha256(raw).hexdigest(),
            "artifacts": {
                p.name: {"bytes": p.stat().st_size, "sha256": _file_hash(p)}
                for p in sorted(staging.iterdir())
            },
        }
        (staging / "manifest.json").write_bytes(canonical_bytes(manifest) + b"\n")
    return manifest


if __name__ == "__main__":
    request = json.load(sys.stdin)
    row = measure_worker(
        Path(request["prepared"]),
        Path(request["source"]),
        Path(request["experiment"]),
        request["detector"],
        request["batch_size"],
        request["mode"],
        BenchmarkConfig(**request["configuration"]),
    )
    print(json.dumps(row, allow_nan=False))
