"""Headless demo and line-oriented controls; stdout is machine-readable JSON."""

import argparse
import json
import shlex
import sys
from collections.abc import Sequence
from decimal import Decimal, DecimalException
from pathlib import Path
from typing import TextIO

from autocan.dbc import PROFILES
from autocan.events import RunEvent
from autocan.faults import FaultSpec
from autocan.scenarios import Scenario
from autocan.simulation import Simulation
from autocan.traces import TraceRecorder, playback, record_scenario, rerun
from autocan.vehicle import validate_throttle


def _time_ns(text: str, *, scale: int = 1_000_000_000) -> int:
    try:
        value = Decimal(text)
        if not value.is_finite() or value < 0:
            raise ValueError
        numerator, denominator = value.as_integer_ratio()
        ns, remainder = divmod(numerator * scale, denominator)
        if remainder:
            raise ValueError
        return ns
    except (DecimalException, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "duration must be nonnegative, finite, and exact in ns"
        ) from exc


def _throttle(text: str) -> float:
    try:
        value = float(text)
        validate_throttle(value)
        return value
    except ValueError as exc:
        raise argparse.ArgumentTypeError("throttle must be finite and in [0, 100]") from exc


def _seed(text: str) -> int:
    try:
        value = int(text)
        if value < 0:
            raise ValueError
        return value
    except ValueError as exc:
        raise argparse.ArgumentTypeError("seed must be a nonnegative integer") from exc


def _write(value: object, *, stream: TextIO) -> None:
    print(json.dumps(value, sort_keys=True, allow_nan=False), file=stream)


def _event(event: RunEvent) -> None:
    _write(event.as_dict(), stream=sys.stdout)


def _shell(sim: Simulation, *, recording: bool = False) -> int:
    if sys.stdin.isatty():
        print(
            "Commands: throttle PERCENT, brake PERCENT, gear P|R|N|D, "
            "run MILLISECONDS, offline [ECU], online [ECU], "
            "ignition OFF|ACC|ON|START, engine start|stop, inject FILE.json, clear FAULT_ID, "
            "pause, resume, step, reset, state, quit",
            file=sys.stderr,
        )
    for line in sys.stdin:
        advancing = False
        try:
            parts = shlex.split(line)
            if not parts:
                continue
            match parts:
                case ["throttle", value]:
                    sim.set_throttle(_throttle(value))
                case ["brake", value]:
                    sim.set_brake(float(value))
                case ["gear", value]:
                    sim.set_gear(value)
                case ["inject", path]:
                    sim.inject_fault(FaultSpec.load(Path(path)))
                case ["clear", identifier]:
                    sim.clear_fault(identifier)
                case ["run", value]:
                    now = sim.state()["sim_time_ns"]
                    assert isinstance(now, int)
                    target = now + _time_ns(value, scale=1_000_000)
                    advancing = True
                    sim.advance_to(target)
                case ["pause"]:
                    sim.pause()
                case ["offline"]:
                    sim.set_engine_publishing(False)
                case ["online"]:
                    sim.set_engine_publishing(True)
                case ["offline", ecu_id]:
                    sim.set_ecu_publishing(ecu_id, False)
                case ["online", ecu_id]:
                    sim.set_ecu_publishing(ecu_id, True)
                case ["ignition", value]:
                    sim.set_ignition(value)
                case ["engine", "start"]:
                    sim.set_engine_running(True)
                case ["engine", "stop"]:
                    sim.set_engine_running(False)
                case ["resume"]:
                    sim.resume()
                case ["step"]:
                    advancing = True
                    sim.step()
                case ["reset"]:
                    if recording:
                        raise ValueError(
                            "reset requires a new recording; exit and start a new shell"
                        )
                    sim.reset()
                case ["state"]:
                    pass
                case ["quit"]:
                    return 0
                case _:
                    raise ValueError("unknown command or wrong number of arguments")
            _write(sim.state(), stream=sys.stdout)
        except (ValueError, OSError, argparse.ArgumentTypeError) as exc:
            if advancing:
                # Core failures abort recording; input/file validation stays recoverable.
                raise
            print(f"error: {exc}", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AutoCAN Studio headless synthetic CAN demo")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="run faster than real time; duration is in seconds")
    demo.add_argument("--duration", type=_time_ns, default=1_000_000_000, metavar="SECONDS")
    demo.add_argument("--throttle", type=_throttle, default=0.0)
    demo.add_argument("--seed", type=_seed, default=0)
    demo.add_argument("--events", action="store_true", help="stream events; summary goes to stderr")
    shell = commands.add_parser("shell", help="line-oriented controls; run takes milliseconds")
    shell.add_argument("--seed", type=_seed, default=0)
    shell.add_argument(
        "--record", type=Path, help="save applied commands/events in a new directory"
    )
    for command_parser in (demo, shell):
        command_parser.add_argument("--profile", choices=tuple(PROFILES), default="democar")
    run = commands.add_parser("run", help="record a declarative JSON scenario into a new directory")
    run.add_argument("scenario", type=Path)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024)
    for name in ("playback", "rerun"):
        command = commands.add_parser(
            name,
            help="verify and view saved events"
            if name == "playback"
            else "recompute and compare a saved run",
        )
        command.add_argument("run_directory", type=Path)
    studio = commands.add_parser("studio", help="build and serve the local M3 dashboard")
    studio.add_argument("--port", type=int, default=8000)
    generate = commands.add_parser("generate", help="generate a bounded, seeded driving dataset")
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--runs", type=int, default=24)
    generate.add_argument("--seed", type=_seed, default=42)
    generate.add_argument(
        "--modes", nargs="+", help="idle stop_and_go city cruise braking acceleration"
    )
    generate.add_argument("--fault-runs", type=int)
    generate.add_argument(
        "--fault-types", nargs="+", help="fault library types to balance across fault runs"
    )
    generate.add_argument("--receiver", default="observer")
    generate.add_argument("--min-duration", type=_time_ns, default=8_000_000_000, metavar="SECONDS")
    generate.add_argument(
        "--max-duration", type=_time_ns, default=12_000_000_000, metavar="SECONDS"
    )
    generate.add_argument("--max-bytes", type=int, default=256 * 1024 * 1024)
    verify_dataset = commands.add_parser("verify-dataset", help="verify a generated dataset series")
    verify_dataset.add_argument("directory", type=Path)
    baseline = commands.add_parser(
        "baseline", help="emit causal features, Tier 0 scores and alerts"
    )
    baseline.add_argument("source", type=Path, help="M7 selected-receiver dataset directory")
    baseline.add_argument("--window", type=_time_ns, default=500_000_000, metavar="SECONDS")
    baseline.add_argument("--heartbeat-periods", type=int, default=5)
    baseline.add_argument("--rpm-derivative-threshold", type=float, default=20_000)
    baseline.add_argument("--wheel-residual-threshold", type=float, default=5)
    benchmark = commands.add_parser(
        "benchmark", help="evaluate fixed Tier 0 rules on whole-run splits"
    )
    benchmark.add_argument("source", type=Path, help="verified M7 factory collection")
    benchmark.add_argument("--output", type=Path, required=True)
    partition = benchmark.add_mutually_exclusive_group()
    partition.add_argument("--split-seed", type=_seed, default=42)
    partition.add_argument("--split", type=Path)
    benchmark.add_argument("--window", type=_time_ns, default=500_000_000, metavar="SECONDS")
    benchmark.add_argument("--heartbeat-periods", type=int, default=5)
    benchmark.add_argument("--rpm-derivative-threshold", type=float, default=20_000)
    benchmark.add_argument("--wheel-residual-threshold", type=float, default=5)
    benchmark.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024)
    ood_generate = commands.add_parser(
        "generate-ood", help="freeze an ID reference and generate two held-out OOD domains"
    )
    ood_generate.add_argument("source", type=Path, help="verified ID factory collection")
    ood_generate.add_argument(
        "--reference", type=Path, required=True, help="completed ID benchmark"
    )
    ood_generate.add_argument("--output", type=Path, required=True)
    ood_generate.add_argument("--seed", type=_seed, default=43)
    ood_generate.add_argument("--fault-runs", type=int, default=6)
    ood_generate.add_argument("--normal-runs", type=int, default=6)
    ood_generate.add_argument("--max-bytes", type=int, default=256 * 1024 * 1024)
    ood_benchmark = commands.add_parser(
        "benchmark-ood", help="evaluate two OOD domains with their frozen ID thresholds"
    )
    ood_benchmark.add_argument("source", type=Path, help="original ID factory collection")
    ood_benchmark.add_argument("suite", type=Path)
    ood_benchmark.add_argument(
        "--reference", type=Path, required=True, help="original ID benchmark"
    )
    ood_benchmark.add_argument("--output", type=Path, required=True)
    ood_benchmark.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024)
    prepare = commands.add_parser(
        "prepare-ml", help="export windows and separate labels with train-only preprocessing"
    )
    prepare.add_argument("source", type=Path)
    prepare.add_argument("--output", type=Path, required=True)
    prepare_partition = prepare.add_mutually_exclusive_group()
    prepare_partition.add_argument("--split-seed", type=_seed, default=42)
    prepare_partition.add_argument("--split", type=Path)
    prepare.add_argument("--window", type=_time_ns, default=500_000_000, metavar="SECONDS")
    prepare.add_argument("--heartbeat-periods", type=int, default=5)
    prepare.add_argument("--max-bytes", type=int, default=64 * 1024 * 1024)
    evaluate_rules = commands.add_parser(
        "evaluate-rules", help="validate saved M9 windows and evaluate fixed rules on their splits"
    )
    evaluate_rules.add_argument("prepared", type=Path)
    evaluate_rules.add_argument(
        "--source", type=Path, required=True, help="original Factory collection"
    )
    evaluate_rules.add_argument("--output", type=Path, required=True)
    evaluate_rules.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024)
    isolation_forest = commands.add_parser(
        "train-isolation-forest",
        help="fit normal train windows, select threshold on validation, compare",
    )
    isolation_forest.add_argument("prepared", type=Path)
    isolation_forest.add_argument("--source", type=Path, required=True)
    isolation_forest.add_argument("--output", type=Path, required=True)
    isolation_forest.add_argument("--seed", type=_seed, default=42)
    isolation_forest.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024)
    random_forest = commands.add_parser(
        "train-random-forest", help="fit labeled train windows and compare both forests with rules"
    )
    random_forest.add_argument("prepared", type=Path)
    random_forest.add_argument("--source", type=Path, required=True)
    random_forest.add_argument("--output", type=Path, required=True)
    random_forest.add_argument("--seed", type=_seed, default=42)
    random_forest.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024)
    ml_ood = commands.add_parser(
        "evaluate-ml-ood", help="score frozen OOD domains with trusted saved forests; no fitting"
    )
    ml_ood.add_argument("prepared", type=Path)
    ml_ood.add_argument("suite", type=Path)
    for option in ("source", "reference", "experiment", "output"):
        ml_ood.add_argument(f"--{option}", type=Path, required=True)
    ml_ood.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024)
    events = commands.add_parser(
        "evaluate-events", help="evaluate fault episodes using saved ID/OOD scores; no inference"
    )
    events.add_argument("prepared", type=Path)
    for option in ("source", "experiment", "ood-results", "suite", "reference", "output"):
        events.add_argument(f"--{option}", type=Path, required=True)
    events.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024)
    inference = commands.add_parser(
        "benchmark-inference", help="benchmark trusted frozen M9 scoring; no fitting or tuning"
    )
    inference.add_argument("prepared", type=Path)
    for option in ("source", "experiment", "output"):
        inference.add_argument(f"--{option}", type=Path, required=True)
    inference.add_argument("--windows", type=int, default=256)
    inference.add_argument("--warmups", type=int, default=2)
    inference.add_argument("--repetitions", type=int, default=5)
    inference.add_argument("--threads", type=int, default=1)
    bootstrap = commands.add_parser(
        "bootstrap-metrics", help="run-bootstrap CIs from saved ID/OOD scores; no inference"
    )
    bootstrap.add_argument("prepared", type=Path)
    for option in ("source", "experiment", "ood-results", "suite", "reference", "output"):
        bootstrap.add_argument(f"--{option}", type=Path, required=True)
    bootstrap.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024)
    bootstrap.add_argument("--replicates", type=int, default=2000)
    bootstrap.add_argument("--seed", type=_seed, default=42)
    for name, help_text in (
        ("trace-export", "export a verified JSONL run to a new Parquet bundle"),
        ("trace-import", "restore a verified Parquet bundle as a new JSONL run"),
        ("dataset", "export one receiver's CAN observations with separate fault labels"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("source", type=Path)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024)
        if name == "dataset":
            command.add_argument("--receiver", default="KOMBI")
    args = parser.parse_args(argv)
    try:
        if args.command == "benchmark-inference":
            from autocan.inference_benchmark import BenchmarkConfig, benchmark_inference

            measured = benchmark_inference(
                args.prepared,
                args.output,
                source=args.source,
                experiment=args.experiment,
                config=BenchmarkConfig(
                    windows=args.windows,
                    warmups=args.warmups,
                    repetitions=args.repetitions,
                    threads=args.threads,
                ),
            )
            _write(measured, stream=sys.stdout)
            return 0
        if args.command == "bootstrap-metrics":
            from autocan.bootstrap_benchmark import bootstrap_saved_metrics

            evaluated = bootstrap_saved_metrics(
                args.prepared,
                args.output,
                source=args.source,
                experiment=args.experiment,
                ood_results=args.ood_results,
                suite=args.suite,
                reference=args.reference,
                max_bytes=args.max_bytes,
                replicates=args.replicates,
                seed=args.seed,
            )
            _write(
                {
                    "status": evaluated["status"],
                    "path": str(args.output),
                    "semantic_sha256": evaluated["semantic_sha256"],
                },
                stream=sys.stdout,
            )
            return 0
        if args.command == "evaluate-events":
            from autocan.event_benchmark import evaluate_saved_events

            evaluated = evaluate_saved_events(
                args.prepared,
                args.output,
                source=args.source,
                experiment=args.experiment,
                ood_results=args.ood_results,
                suite=args.suite,
                reference=args.reference,
                max_bytes=args.max_bytes,
            )
            _write(
                {
                    "status": evaluated["status"],
                    "path": str(args.output),
                    "semantic_sha256": evaluated["semantic_sha256"],
                },
                stream=sys.stdout,
            )
            return 0
        if args.command == "evaluate-ml-ood":
            from autocan.ml_ood import evaluate_ml_ood

            evaluated = evaluate_ml_ood(
                args.prepared,
                args.suite,
                args.output,
                source=args.source,
                reference=args.reference,
                experiment=args.experiment,
                max_bytes=args.max_bytes,
            )
            _write(
                {
                    "status": evaluated["status"],
                    "path": str(args.output),
                    "semantic_sha256": evaluated["semantic_sha256"],
                },
                stream=sys.stdout,
            )
            return 0
        if args.command in ("train-isolation-forest", "train-random-forest"):
            from autocan.ml_experiment import run_isolation_forest, run_random_forest

            run_experiment = (
                run_random_forest if args.command == "train-random-forest" else run_isolation_forest
            )
            experiment = run_experiment(
                args.prepared,
                args.output,
                source=args.source,
                seed=args.seed,
                max_bytes=args.max_bytes,
            )
            _write(
                {
                    "status": experiment["status"],
                    "path": str(args.output),
                    "semantic_sha256": experiment["semantic_sha256"],
                },
                stream=sys.stdout,
            )
            return 0
        if args.command == "evaluate-rules":
            from autocan.window_benchmark import evaluate_saved_rules

            evaluated = evaluate_saved_rules(
                args.prepared, args.output, source=args.source, max_bytes=args.max_bytes
            )
            _write(
                {
                    "status": evaluated["status"],
                    "path": str(args.output),
                    "semantic_sha256": evaluated["semantic_sha256"],
                },
                stream=sys.stdout,
            )
            return 0
        if args.command == "prepare-ml":
            from autocan.features import FeatureConfig
            from autocan.training_data import prepare_training_data

            prepared = prepare_training_data(
                args.source,
                args.output,
                config=FeatureConfig(
                    window_ns=args.window, heartbeat_periods=args.heartbeat_periods
                ),
                split_seed=args.split_seed,
                split_path=args.split,
                max_bytes=args.max_bytes,
            )
            _write(
                {
                    "status": prepared["status"],
                    "path": str(args.output),
                    "rows": prepared["rows"],
                    "semantic_sha256": prepared["semantic_sha256"],
                },
                stream=sys.stdout,
            )
            return 0
        if args.command in ("generate-ood", "benchmark-ood"):
            from autocan.ood import generate_ood
            from autocan.ood_benchmark import evaluate_ood

            if args.command == "generate-ood":
                ood_meta = generate_ood(
                    args.source,
                    args.reference,
                    args.output,
                    seed=args.seed,
                    fault_runs=args.fault_runs,
                    normal_runs=args.normal_runs,
                    max_bytes=args.max_bytes,
                )
            else:
                ood_meta = evaluate_ood(
                    args.source, args.reference, args.suite, args.output, max_bytes=args.max_bytes
                )
            _write(
                {
                    "status": ood_meta["status"],
                    "path": str(args.output),
                    "semantic_sha256": ood_meta["semantic_sha256"],
                },
                stream=sys.stdout,
            )
            return 0
        if args.command == "benchmark":
            from autocan.benchmark import run_benchmark
            from autocan.features import FeatureConfig

            benchmark_meta = run_benchmark(
                args.source,
                args.output,
                config=FeatureConfig(
                    window_ns=args.window,
                    heartbeat_periods=args.heartbeat_periods,
                    rpm_derivative_threshold=args.rpm_derivative_threshold,
                    wheel_residual_threshold=args.wheel_residual_threshold,
                ),
                split_seed=args.split_seed,
                split_path=args.split,
                max_bytes=args.max_bytes,
            )
            _write(
                {
                    "status": benchmark_meta["status"],
                    "path": str(args.output),
                    "semantic_sha256": benchmark_meta["semantic_sha256"],
                },
                stream=sys.stdout,
            )
            return 0
        if args.command == "baseline":
            from dataclasses import asdict

            from autocan.clock import SimulationClock
            from autocan.features import (
                DETECTOR_ID,
                FEATURE_SCHEMA_VERSION,
                BaselineDetector,
                FeatureConfig,
                FeaturePipeline,
                FeatureWindow,
                feature_record,
                replay_observations,
            )
            from autocan.parquet import open_observer

            config = FeatureConfig(
                window_ns=args.window,
                heartbeat_periods=args.heartbeat_periods,
                rpm_derivative_threshold=args.rpm_derivative_threshold,
                wheel_residual_threshold=args.wheel_residual_threshold,
            )
            detector = BaselineDetector(config)

            def emit_window(window: FeatureWindow) -> None:
                _write(feature_record(window), stream=sys.stdout)
                for record in detector.evaluate(window):
                    _write(record, stream=sys.stdout)

            with open_observer(args.source) as source:
                clock = SimulationClock()
                pipeline = FeaturePipeline(
                    clock,
                    profile_id=source.profile_id,
                    receiver_id=source.receiver_id,
                    end_ns=source.end_ns,
                    config=config,
                    on_window=emit_window,
                )
                _write(
                    {
                        "kind": "baseline_config",
                        "config": asdict(config),
                        "profile_id": source.profile_id,
                        "receiver_id": source.receiver_id,
                        "end_ns": source.end_ns,
                        "detector_id": DETECTOR_ID,
                        "feature_schema_version": FEATURE_SCHEMA_VERSION,
                    },
                    stream=sys.stdout,
                )
                replay_observations(source.observations, clock, pipeline, end_ns=source.end_ns)
            return 0
        if args.command in ("generate", "verify-dataset"):
            from autocan.factory import FactoryConfig, generate_factory, verify_factory

            if args.command == "generate":
                options = {
                    "runs": args.runs,
                    "seed": args.seed,
                    "fault_runs": args.fault_runs,
                    "receiver_id": args.receiver,
                    "min_duration_ns": args.min_duration,
                    "max_duration_ns": args.max_duration,
                    "max_bytes": args.max_bytes,
                }
                for name in ("modes", "fault_types"):
                    if getattr(args, name) is not None:
                        options[name] = getattr(args, name)
                generated = generate_factory(FactoryConfig.from_dict(options), args.output)
            else:
                generated = verify_factory(args.directory)
            _write(
                {
                    "status": generated["status"],
                    "runs": len(generated["runs"]),
                    "semantic_sha256": generated["semantic_sha256"],
                },
                stream=sys.stdout,
            )
            return 0
        if args.command in ("trace-export", "trace-import", "dataset"):
            from autocan.parquet import export_observer, export_trace, import_trace

            if args.command == "dataset":
                meta = export_observer(
                    args.source, args.output, receiver_id=args.receiver, max_bytes=args.max_bytes
                )
            else:
                operation = export_trace if args.command == "trace-export" else import_trace
                meta = operation(args.source, args.output, max_bytes=args.max_bytes)
            _write({"status": meta["status"], "path": str(args.output)}, stream=sys.stdout)
            return 0
        if args.command == "studio":
            from autocan.api.launch import launch

            return launch(port=args.port)
        if args.command == "run":
            meta = record_scenario(
                Scenario.load(args.scenario), args.output, max_bytes=args.max_bytes
            )
            _write(
                {
                    "status": meta["status"],
                    "path": str(args.output),
                    "semantic_sha256": meta["semantic_sha256"],
                    "final_state": meta["final_state"],
                },
                stream=sys.stdout,
            )
            return 0
        if args.command == "playback":
            for record in playback(args.run_directory):
                _write(record, stream=sys.stdout)
            return 0
        if args.command == "rerun":
            _write(rerun(args.run_directory), stream=sys.stdout)
            return 0
        if args.command == "shell" and args.record is not None:
            with TraceRecorder(args.record) as recorder:
                with Simulation(
                    seed=args.seed, profile_id=args.profile, on_event=recorder.write
                ) as sim:
                    recorder.begin(sim)
                    result = _shell(sim, recording=True)
                    recorder.finish(sim.state())
                    return result
        sink = _event if args.command == "demo" and args.events else None
        with Simulation(seed=args.seed, on_event=sink, profile_id=args.profile) as sim:
            if args.command == "shell":
                return _shell(sim)
            sim.set_throttle(args.throttle)
            sim.advance_to(args.duration)
            _write(sim.state(), stream=sys.stderr if args.events else sys.stdout)
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
