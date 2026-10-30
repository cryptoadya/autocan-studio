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
from autocan.scenarios import Scenario
from autocan.simulation import Simulation
from autocan.traces import playback, record_scenario, rerun
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


def _shell(sim: Simulation) -> int:
    if sys.stdin.isatty():
        print(
            "Commands: throttle PERCENT, brake PERCENT, gear P|R|N|D, "
            "run MILLISECONDS, offline [ECU], online [ECU], "
            "ignition OFF|ACC|ON|START, engine start|stop, pause, resume, step, reset, state, quit",
            file=sys.stderr,
        )
    for line in sys.stdin:
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
                case ["run", value]:
                    now = sim.state()["sim_time_ns"]
                    assert isinstance(now, int)
                    sim.advance_to(now + _time_ns(value, scale=1_000_000))
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
                    sim.step()
                case ["reset"]:
                    sim.reset()
                case ["state"]:
                    pass
                case ["quit"]:
                    return 0
                case _:
                    raise ValueError("unknown command or wrong number of arguments")
            _write(sim.state(), stream=sys.stdout)
        except (ValueError, argparse.ArgumentTypeError) as exc:
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
    args = parser.parse_args(argv)
    try:
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
