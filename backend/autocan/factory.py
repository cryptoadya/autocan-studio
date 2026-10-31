"""Seeded synthetic driving recipes and bounded, verifiable run collections."""

from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from random import Random
from typing import TypedDict, cast

from autocan.faults import FAULT_TYPES, FaultSpec
from autocan.network_profile import PLAN_BY_NAME, RECEIVERS
from autocan.parquet import TABLES, _file_hash, _new_output, export_observer, export_trace
from autocan.scenarios import Scenario
from autocan.simulation import PHYSICS_TICK_NS
from autocan.traces import (
    ARTIFACTS,
    DEFAULT_MAX_BYTES,
    TraceError,
    _hash,
    record_scenario,
    verify_trace,
)
from autocan.validation import canonical_bytes, fields, integer, json_object, object_value

GENERATOR_VERSION = "m7-driving-1"
DRIVING_MODES = ("idle", "stop_and_go", "city", "cruise", "braking", "acceleration")
DEFAULT_FAULT_TYPES = ("ecu_offline", "signal_spike", "message_delay")
MAX_RUNS = 300
DEFAULT_FACTORY_BYTES = 256 * 1024 * 1024
MAX_FACTORY_BYTES = 1024 * 1024 * 1024
MAX_FACTORY_MANIFEST_BYTES = 2 * 1024 * 1024
RUN_FILES = (
    "scenario.json",
    *(f"trace/{name}" for name in ("manifest.json", *ARTIFACTS)),
    *(f"parquet/{name}" for name in ("manifest.json", "profile.dbc", *TABLES)),
    "observer/manifest.json",
    "observer/observations.parquet",
    "observer/labels/faults.parquet",
)


def _choices(value: object, allowed: tuple[str, ...], name: str) -> tuple[str, ...]:
    if (
        not isinstance(value, (tuple, list))
        or not value
        or any(not isinstance(item, str) or item not in allowed for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"{name} must be a nonempty unique selection from {', '.join(allowed)}")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class FactoryConfig:
    runs: int = 24
    seed: int = 42
    modes: tuple[str, ...] = DRIVING_MODES
    fault_runs: int | None = None
    fault_types: tuple[str, ...] = DEFAULT_FAULT_TYPES
    receiver_id: str = "observer"
    min_duration_ns: int = 8_000_000_000
    max_duration_ns: int = 12_000_000_000
    max_bytes: int = DEFAULT_FACTORY_BYTES

    def __post_init__(self) -> None:
        if integer(self.runs, "runs", minimum=1) > MAX_RUNS:
            raise ValueError(f"runs must be <= {MAX_RUNS}")
        if integer(self.seed, "seed") > 2**63 - 1:
            raise ValueError("seed must fit a nonnegative signed 64-bit integer")
        count = self.runs // 4 if self.fault_runs is None else self.fault_runs
        if integer(count, "fault_runs") > self.runs:
            raise ValueError("fault_runs must be <= runs")
        object.__setattr__(self, "fault_runs", count)
        object.__setattr__(self, "modes", _choices(self.modes, DRIVING_MODES, "modes"))
        object.__setattr__(
            self, "fault_types", _choices(self.fault_types, FAULT_TYPES, "fault_types")
        )
        if self.receiver_id not in RECEIVERS:
            raise ValueError("unsupported receiver")
        lo = integer(self.min_duration_ns, "min_duration_ns", minimum=8_000_000_000)
        hi = integer(self.max_duration_ns, "max_duration_ns")
        if not lo <= hi <= 30_000_000_000 or lo % PHYSICS_TICK_NS or hi % PHYSICS_TICK_NS:
            raise ValueError(
                "duration bounds must align to 10 ms and satisfy 8 <= min <= max <= 30 s"
            )
        if integer(self.max_bytes, "max_bytes", minimum=4096) > MAX_FACTORY_BYTES:
            raise ValueError("series quota must be <= 1 GiB")

    def as_dict(self) -> dict[str, object]:
        return {
            "runs": self.runs,
            "seed": self.seed,
            "modes": list(self.modes),
            "fault_runs": self.fault_runs,
            "fault_types": list(self.fault_types),
            "receiver_id": self.receiver_id,
            "min_duration_ns": self.min_duration_ns,
            "max_duration_ns": self.max_duration_ns,
            "max_bytes": self.max_bytes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "FactoryConfig":
        defaults = cls().as_dict()
        fields(data, set(), set(defaults))
        values = defaults | data
        # A partial config with a new run count uses the normal 25% default.
        count = data.get("fault_runs")
        receiver = values["receiver_id"]
        if not isinstance(receiver, str):
            raise ValueError("receiver_id must be a string")
        return cls(
            runs=integer(values["runs"], "runs", minimum=1),
            seed=integer(values["seed"], "seed"),
            modes=_choices(values["modes"], DRIVING_MODES, "modes"),
            fault_runs=None if count is None else integer(count, "fault_runs"),
            fault_types=_choices(values["fault_types"], FAULT_TYPES, "fault_types"),
            receiver_id=receiver,
            min_duration_ns=integer(values["min_duration_ns"], "min_duration_ns"),
            max_duration_ns=integer(values["max_duration_ns"], "max_duration_ns"),
            max_bytes=integer(values["max_bytes"], "max_bytes"),
        )


class RunRecipe(TypedDict):
    index: int
    mode: str
    seed: int
    fault_type: str | None
    scenario: dict[str, object]


class RunSummary(TypedDict):
    index: int
    path: str
    run_id: str
    mode: str
    seed: int
    duration_ns: int
    fault_type: str | None
    scenario_sha256: str
    trace_semantic_sha256: str
    observation_semantic_sha256: str


class FileInfo(TypedDict):
    bytes: int
    sha256: str


class FactoryManifest(TypedDict):
    format: str
    format_version: int
    generator_version: str
    status: str
    configuration: dict[str, object]
    runs: list[RunSummary]
    artifacts: dict[str, FileInfo]
    semantic_sha256: str


def _seed(seed: int, stream: str, index: int = 0) -> int:
    raw = sha256(canonical_bytes([GENERATOR_VERSION, seed, stream, index])).digest()
    return int.from_bytes(raw[:8], "big") & (2**63 - 1)


def _driving_commands(mode: str, duration: int, rng: Random) -> list[dict[str, object]]:
    commands: list[dict[str, object]] = []

    def apply(at: int, kind: str, value: str | int) -> None:
        parameter = (
            "gear" if kind == "set_gear" else "state" if kind == "set_ignition" else "percent"
        )
        commands.append({"at_ns": at, "kind": kind, parameter: value})

    def phase(percent: int) -> int:
        ticks = duration // PHYSICS_TICK_NS
        return (ticks * percent // 100 + rng.randint(-4, 4)) * PHYSICS_TICK_NS

    def pedals(at: int, throttle: int, brake: int = 0) -> None:
        apply(at, "set_throttle", throttle)
        apply(at, "set_brake", brake)

    apply(0, "set_ignition", "START")
    apply(0, "set_brake", 100)
    if mode == "idle":
        return commands
    apply(0, "set_gear", "D")
    start = rng.randint(30, 50) * PHYSICS_TICK_NS
    pedals(start, rng.randint(45, 70))
    if mode == "acceleration":
        pedals(phase(45), rng.randint(75, 90))
    elif mode == "cruise":
        # With the fixed model, 5% throttle exactly balances rolling resistance.
        pedals(phase(30), 5)
    elif mode == "braking":
        pedals(phase(50), 0, rng.randint(50, 90))
        pedals(phase(80), 0, 100)
    elif mode == "city":
        pedals(phase(30), 5)
        pedals(phase(45), 0, 100)
        apply(phase(60), "set_gear", "N")
        apply(phase(65), "set_gear", "D")
        pedals(phase(72), rng.randint(35, 60))
        pedals(phase(88), 0, 100)
    elif mode == "stop_and_go":
        for percent, throttle in (
            (20, 0),
            (38, rng.randint(35, 65)),
            (53, 0),
            (70, rng.randint(35, 65)),
            (85, 0),
        ):
            pedals(phase(percent), throttle, 100 if throttle == 0 else 0)
    return commands


def _fault(kind: str, rng: Random, seed: int) -> FaultSpec:
    wheel = kind in {"ecu_offline", "message_drop", "cross_signal_inconsistency"}
    message = PLAN_BY_NAME["WheelSpeeds" if wheel else "EngineStatus"]
    params: dict[str, float | int] = {}
    signal: str | None = None
    if kind == "message_drop":
        params = {"probability": rng.randint(50, 90) / 100}
    elif kind == "message_delay":
        params = {"min_ns": 0, "max_ns": rng.randint(8, 20) * PHYSICS_TICK_NS}
    elif kind == "message_frequency_anomaly":
        params = {"period_ns": rng.randint(6, 12) * PHYSICS_TICK_NS}
    elif kind == "signal_spike":
        signal, params = "EngineRpm", {"value": rng.randint(65, 78) * 100}
    elif kind == "signal_stuck":
        signal = "EngineRpm"
    elif kind == "signal_bias":
        signal, params = "EngineRpm", {"offset": rng.randint(2, 5) * 100}
    elif kind == "cross_signal_inconsistency":
        signal, params = "FrontLeft", {"offset": rng.randint(20, 40)}
    return FaultSpec(
        "generated-fault",
        kind,
        message.source_ecu,
        None if kind == "ecu_offline" else message.can_id,
        parameters=params,
        signal=signal,
        seed=seed,
    )


def generate_recipes(config: FactoryConfig) -> Iterator[RunRecipe]:
    """Balanced mode cycles; motion and fault planning have independent RNG streams."""
    mode_rng, selection_rng = (
        Random(_seed(config.seed, "modes")),
        Random(_seed(config.seed, "selection")),
    )
    assert config.fault_runs is not None
    indices = sorted(selection_rng.sample(range(config.runs), config.fault_runs))
    fault_order = list(config.fault_types)
    selection_rng.shuffle(fault_order)
    selected = {index: fault_order[i % len(fault_order)] for i, index in enumerate(indices)}
    modes: list[str] = []
    for index in range(config.runs):
        if index % len(config.modes) == 0:
            modes = list(config.modes)
            mode_rng.shuffle(modes)
        mode = modes[index % len(modes)]
        rng = Random(_seed(config.seed, "motion", index))
        duration = (
            rng.randint(
                config.min_duration_ns // PHYSICS_TICK_NS,
                config.max_duration_ns // PHYSICS_TICK_NS,
            )
            * PHYSICS_TICK_NS
        )
        commands = _driving_commands(mode, duration, rng)
        kind = selected.get(index)
        if kind is not None:
            fault_rng = Random(_seed(config.seed, "fault", index))
            ticks = duration // PHYSICS_TICK_NS
            start = fault_rng.randint(ticks * 30 // 100, ticks * 60 // 100) * PHYSICS_TICK_NS
            end = start + fault_rng.randint(ticks // 10, ticks // 5) * PHYSICS_TICK_NS
            spec = _fault(kind, fault_rng, _seed(config.seed, "injection", index))
            commands.extend(
                [
                    {"at_ns": start, "kind": "inject_fault", "fault": spec.as_dict()},
                    {"at_ns": end, "kind": "clear_fault", "fault_id": spec.fault_id},
                ]
            )
        document: dict[str, object] = {
            "schema_version": 1,
            "profile_id": "democar-network",
            "duration_ns": duration,
            "seed": _seed(config.seed, "simulation", index),
            "commands": sorted(commands, key=lambda c: integer(c["at_ns"], "command time")),
        }
        Scenario.from_dict(document)
        yield {
            "index": index,
            "mode": mode,
            "seed": integer(document["seed"], "seed"),
            "fault_type": kind,
            "scenario": document,
        }


def _series_hash(config: FactoryConfig, runs: list[RunSummary]) -> str:
    return sha256(
        canonical_bytes(
            {
                "generator_version": GENERATOR_VERSION,
                "configuration": config.as_dict(),
                "runs": [
                    {key: value for key, value in row.items() if key not in {"run_id", "path"}}
                    for row in runs
                ],
            }
        )
    ).hexdigest()


def _remaining(config: FactoryConfig, used: int) -> int:
    remaining = config.max_bytes - used
    if remaining < 4096:
        raise TraceError("dataset series quota exceeded")
    return min(remaining, DEFAULT_MAX_BYTES)


def _size(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def generate_factory(config: FactoryConfig, target: Path) -> FactoryManifest:
    """Generate one run at a time; publish the collection only after all succeed."""
    with _new_output(target) as staging:
        summaries: list[RunSummary] = []
        artifacts: dict[str, FileInfo] = {}
        used = 0
        for recipe in generate_recipes(config):
            relative = f"runs/{recipe['index']:04d}"
            root = staging / relative
            root.mkdir(parents=True)
            raw = canonical_bytes(recipe["scenario"]) + b"\n"
            (root / "scenario.json").write_bytes(raw)
            used += len(raw)
            trace_meta = record_scenario(
                Scenario.from_dict(recipe["scenario"]),
                root / "trace",
                max_bytes=_remaining(config, used),
            )
            used += _size(root / "trace")
            export_trace(root / "trace", root / "parquet", max_bytes=_remaining(config, used))
            used += _size(root / "parquet")
            observed = export_observer(
                root / "trace",
                root / "observer",
                receiver_id=config.receiver_id,
                max_bytes=_remaining(config, used),
            )
            used += _size(root / "observer")
            observation = object_value(
                object_value(observed["artifacts"], "artifacts")["observations.parquet"],
                "observations",
            )
            summaries.append(
                {
                    "index": recipe["index"],
                    "path": relative,
                    "run_id": str(trace_meta["run_id"]),
                    "mode": recipe["mode"],
                    "seed": recipe["seed"],
                    "duration_ns": integer(recipe["scenario"]["duration_ns"], "duration"),
                    "fault_type": recipe["fault_type"],
                    "scenario_sha256": sha256(raw).hexdigest(),
                    "trace_semantic_sha256": str(trace_meta["semantic_sha256"]),
                    "observation_semantic_sha256": str(observation["semantic_sha256"]),
                }
            )
            for name in RUN_FILES:
                path = root / name
                artifacts[f"{relative}/{name}"] = {
                    "bytes": path.stat().st_size,
                    "sha256": _file_hash(path),
                }
        meta: FactoryManifest = {
            "format": "autocan-dataset-factory",
            "format_version": 1,
            "generator_version": GENERATOR_VERSION,
            "status": "complete",
            "configuration": config.as_dict(),
            "runs": summaries,
            "artifacts": artifacts,
            "semantic_sha256": _series_hash(config, summaries),
        }
        raw = canonical_bytes(meta) + b"\n"
        if len(raw) > MAX_FACTORY_MANIFEST_BYTES:
            raise TraceError("dataset manifest exceeds size limit")
        if used + len(raw) > config.max_bytes:
            raise TraceError("dataset series quota exceeded")
        (staging / "manifest.json").write_bytes(raw)
    return meta


def _safe_relative(root: Path, name: str) -> Path:
    path = root
    for part in Path(name).parts:
        path /= part
        if path.is_symlink():
            raise TraceError(f"symlink artifact rejected: {name}")
    if not path.is_file():
        raise TraceError(f"missing artifact: {name}")
    return path


def _read_json(path: Path, limit: int = MAX_FACTORY_MANIFEST_BYTES) -> dict[str, object]:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise TraceError("dataset manifest exceeds size limit")
    return json_object(raw.decode())


def verify_factory(root: Path) -> FactoryManifest:
    """Verify the inventory, seeded recipes, source traces and logical series hash."""
    try:
        manifest_path = _safe_relative(root, "manifest.json")
        meta = _read_json(manifest_path)
        fields(
            meta,
            {
                "format",
                "format_version",
                "generator_version",
                "status",
                "configuration",
                "runs",
                "artifacts",
                "semantic_sha256",
            },
        )
        if (
            meta["format"] != "autocan-dataset-factory"
            or meta["status"] != "complete"
            or integer(meta["format_version"], "format version") != 1
            or meta["generator_version"] != GENERATOR_VERSION
        ):
            raise TraceError("unsupported or incomplete dataset factory")
        config = FactoryConfig.from_dict(object_value(meta["configuration"], "configuration"))
        if config.as_dict() != meta["configuration"]:
            raise TraceError("noncanonical factory configuration")
        rows = meta["runs"]
        if not isinstance(rows, list) or len(rows) != config.runs:
            raise TraceError("run count mismatch")
        inventory = object_value(meta["artifacts"], "artifacts")
        expected = {f"runs/{i:04d}/{name}" for i in range(config.runs) for name in RUN_FILES}
        if inventory.keys() != expected:
            raise TraceError("unsupported dataset artifact paths")
        total = manifest_path.stat().st_size
        for name, value in inventory.items():
            info = object_value(value, "artifact")
            fields(info, {"bytes", "sha256"})
            size = integer(info["bytes"], "artifact size")
            total += size
            if total > config.max_bytes:
                raise TraceError("dataset series quota exceeded")
            path = _safe_relative(root, name)
            if path.stat().st_size != size or _file_hash(path) != _hash(info["sha256"]):
                raise TraceError(f"artifact checksum or size mismatch: {name}")
        for value, recipe in zip(rows, generate_recipes(config), strict=True):
            row = object_value(value, "run")
            fields(
                row,
                {
                    "index",
                    "path",
                    "run_id",
                    "mode",
                    "seed",
                    "duration_ns",
                    "fault_type",
                    "scenario_sha256",
                    "trace_semantic_sha256",
                    "observation_semantic_sha256",
                },
            )
            relative = f"runs/{recipe['index']:04d}"
            source = root / relative
            scenario_raw = canonical_bytes(recipe["scenario"]) + b"\n"
            trace_meta = verify_trace(source / "trace")
            observed = _read_json(source / "observer/manifest.json")
            parquet = _read_json(source / "parquet/manifest.json")
            observation = object_value(
                object_value(observed["artifacts"], "artifacts")["observations.parquet"],
                "observations",
            )
            if (
                row
                != {
                    "index": recipe["index"],
                    "path": relative,
                    "run_id": trace_meta["run_id"],
                    "mode": recipe["mode"],
                    "seed": recipe["seed"],
                    "duration_ns": recipe["scenario"]["duration_ns"],
                    "fault_type": recipe["fault_type"],
                    "scenario_sha256": sha256(scenario_raw).hexdigest(),
                    "trace_semantic_sha256": trace_meta["semantic_sha256"],
                    "observation_semantic_sha256": observation["semantic_sha256"],
                }
                or _file_hash(source / "scenario.json") != row["scenario_sha256"]
                or observed.get("receiver_id") != config.receiver_id
                or observed.get("trace_manifest") != trace_meta
                or parquet.get("trace_manifest") != trace_meta
                or trace_meta["end_time_ns"] != row["duration_ns"]
                or object_value(trace_meta["configuration"], "configuration")["seed"] != row["seed"]
            ):
                raise TraceError("dataset recipe or provenance mismatch")
        result = cast(FactoryManifest, meta)
        if _series_hash(config, result["runs"]) != _hash(result["semantic_sha256"]):
            raise TraceError("dataset semantic checksum mismatch")
        return result
    except (ValueError, OSError, KeyError) as exc:
        raise TraceError(str(exc)) from exc
