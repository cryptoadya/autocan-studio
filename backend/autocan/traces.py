"""Bounded JSONL recording, verified playback, and a fresh simulation rerun."""

import json
import platform
import subprocess
from collections.abc import Iterator
from hashlib import sha256
from heapq import merge
from importlib.metadata import version
from importlib.resources import files
from itertools import groupby
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Self, TypedDict

from autocan.dbc import profile_bytes, profile_from_version, profile_version
from autocan.events import CanFrame, RunEvent, Scalar
from autocan.faults import FaultSpec
from autocan.network_profile import (
    ECU_IDS,
    GEARS,
    PLAN_BY_ID,
    PLAN_BY_NAME,
    RECEIVERS,
    SHIFT_REASONS,
)
from autocan.scenarios import MAX_COMMANDS, Scenario, ScenarioCommand, command_parameter
from autocan.simulation import Simulation
from autocan.validation import canonical_bytes, fields, integer, json_object, number, object_value

CORE_VERSION = "m6-faults-1"
DEFAULT_MAX_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_LINE_BYTES = 64 * 1024
STREAMS = ("events.jsonl", "commands.jsonl", "faults.jsonl")
ARTIFACTS = (*STREAMS, "profile.dbc")
FAULT_KINDS = {"FaultActivated", "FaultCleared", "FrameDropped"}


class TraceError(ValueError):
    """An incomplete, incompatible, corrupt, or oversized recording."""


class LiveModelProvenance(TypedDict):
    model_id: str
    model_sha256: str
    threshold: float
    feature_schema_version: int


def _stream_name(kind: object) -> str:
    if not isinstance(kind, str):
        raise TraceError("event kind must be a string")
    return (
        "commands.jsonl"
        if kind == "InputApplied"
        else "faults.jsonl"
        if kind in FAULT_KINDS
        else "events.jsonl"
    )


def _semantic(record: dict[str, object]) -> bytes:
    return canonical_bytes({k: v for k, v in record.items() if k != "run_id"}) + b"\n"


def _profile_bytes(profile_id: str = "democar") -> bytes:
    return profile_bytes(profile_id)


def _core_hash() -> str:
    digest = sha256()
    for name in (
        "clock",
        "vehicle",
        "events",
        "bus",
        "ecu",
        "simulation",
        "scenarios",
        "validation",
        "dbc",
        "network_profile",
        "network_ecu",
        "network_runtime",
        "faults",
    ):
        digest.update(name.encode() + b"\0")
        digest.update(files("autocan").joinpath(f"{name}.py").read_bytes())
    return digest.hexdigest()


def _packages() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        **{name: version(name) for name in ("autocan-studio", "cantools", "python-can")},
    }


def _git_provenance() -> dict[str, object]:
    root = Path(__file__).resolve().parents[2]
    result: dict[str, object] = {"git_commit": None, "git_dirty": None}
    if not (root / "backend/autocan/traces.py").is_file():
        return result
    try:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if commit.returncode == status.returncode == 0:
            result.update(git_commit=commit.stdout.strip(), git_dirty=bool(status.stdout))
    except OSError:
        pass
    return result


class TraceRecorder:
    """Create one new run directory; never overwrite or span a simulation reset."""

    def __init__(self, path: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        integer(max_bytes, "max_bytes", minimum=4096)
        if max_bytes > DEFAULT_MAX_BYTES:
            raise TraceError("trace quota exceeds the supported import limit")
        self.path = path
        self._limit = max_bytes
        self._files: dict[str, BinaryIO] = {}
        self._hashes = {name: sha256() for name in ARTIFACTS}
        self._sizes = dict.fromkeys(ARTIFACTS, 0)
        self._counts = dict.fromkeys(ARTIFACTS, 0)
        self._semantic = sha256()
        self._last_seq = self._last_time = 0
        self._started = self._finished = False
        self._meta: dict[str, object] = {"format_version": 1, "status": "recording"}
        path.mkdir(parents=True, exist_ok=False)
        try:
            self._save_manifest()
            for name in STREAMS:
                self._files[name] = (path / name).open("xb")
        except Exception as exc:
            self._abort(str(exc))
            raise

    def begin(
        self,
        sim: Simulation,
        *,
        scenario: dict[str, object] | None = None,
        live_model: LiveModelProvenance | None = None,
    ) -> None:
        if (
            self._started
            or self._finished
            or sim.state()["sim_time_ns"] != 0
            or sim.state()["events_emitted"] != 0
        ):
            raise TraceError("recording must start at a fresh run")
        configuration = sim.configuration()
        initial = sim.state()
        profile = _profile_bytes(sim.profile_id)
        try:
            (self.path / "profile.dbc").write_bytes(profile)
            self._hashes["profile.dbc"].update(profile)
            self._sizes["profile.dbc"] = len(profile)
        except Exception as exc:
            self._abort(str(exc))
            raise
        self._meta.update(
            run_id=sim.run_id,
            schema_version=1,
            core_version=CORE_VERSION,
            core_sha256=_core_hash(),
            profile_version=profile_version(sim.profile_id),
            profile_sha256=sha256(profile).hexdigest(),
            package_versions=_packages(),
            configuration=configuration,
            initial_state=initial,
            config_sha256=sha256(
                canonical_bytes({"configuration": configuration, "initial_state": initial})
            ).hexdigest(),
            **_git_provenance(),
        )
        self._started = True
        if scenario is not None:
            self._meta["scenario"] = dict(scenario)
        if live_model is not None:
            self._meta["live_model"] = dict(live_model)
        self._save_manifest()

    def write(self, event: RunEvent) -> None:
        if not self._started or self._finished or event.run_id != self._meta["run_id"]:
            raise TraceError("recording is not active or run identity changed")
        record = event.as_dict()
        if event.event_seq != self._last_seq + 1 or event.sim_time_ns < self._last_time:
            raise TraceError("event ordering changed")
        raw = canonical_bytes(record) + b"\n"
        if len(raw) > MAX_LINE_BYTES or sum(self._sizes.values()) + len(raw) > self._limit:
            raise TraceError("trace quota exceeded")
        name = _stream_name(event.event_kind)
        self._files[name].write(raw)
        self._hashes[name].update(raw)
        self._sizes[name] += len(raw)
        self._counts[name] += 1
        self._semantic.update(_semantic(record))
        self._last_seq, self._last_time = event.event_seq, event.sim_time_ns

    def finish(self, state: dict[str, Scalar]) -> dict[str, object]:
        if not self._started or self._finished:
            raise TraceError("recording is not active")
        if not self._last_seq or state.get("events_emitted") != self._last_seq:
            raise TraceError("recording has no initial events or simulation was reset")
        end = integer(state["sim_time_ns"], "end time")
        if end < self._last_time:
            raise TraceError("end time precedes an event")
        self._close_files()
        self._meta.update(
            status="complete",
            end_time_ns=end,
            final_state=state,
            semantic_sha256=self._semantic.hexdigest(),
            artifacts={
                name: {
                    "sha256": self._hashes[name].hexdigest(),
                    "bytes": self._sizes[name],
                    "records": self._counts[name],
                }
                for name in ARTIFACTS
            },
        )
        self._save_manifest()
        self._finished = True
        return dict(self._meta)

    def _save_manifest(self) -> None:
        raw = json.dumps(self._meta, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
        if len(raw) > MAX_MANIFEST_BYTES:
            raise TraceError("manifest exceeds size limit")
        temporary = self.path / "manifest.tmp"
        temporary.write_bytes(raw)
        temporary.replace(self.path / "manifest.json")

    def _close_files(self) -> None:
        for stream in self._files.values():
            stream.close()

    def _abort(self, reason: str) -> None:
        try:
            self._close_files()
        finally:
            self._meta.update(status="failed", error=reason[:512])
            self._save_manifest()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self._finished:
            self._abort(str(exc) if exc is not None else "recording was not finalized")


def record_scenario(
    scenario: Scenario, path: Path, *, max_bytes: int = DEFAULT_MAX_BYTES
) -> dict[str, object]:
    with TraceRecorder(path, max_bytes=max_bytes) as recorder:
        with Simulation(
            seed=scenario.seed,
            profile_id=scenario.profile_id,
            delivery_delay_ns=scenario.delivery_delay_ns,
            drop_probability=scenario.drop_probability,
            on_event=recorder.write,
        ) as sim:
            recorder.begin(sim)
            for command in scenario.commands:
                command.schedule(sim)
            sim.advance_to(scenario.duration_ns)
            return recorder.finish(sim.state())


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise TraceError(f"invalid {name}")
    return value


def _hash(value: object) -> str:
    text = _text(value, "SHA256")
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        raise TraceError("invalid SHA256")
    return text


def _safe_file(root: Path, name: str) -> Path:
    path = root / name
    if path.is_symlink():
        raise TraceError(f"symlink artifact rejected: {name}")
    if not path.is_file():
        raise TraceError(f"missing artifact: {name}")
    return path


def _read_meta(root: Path) -> dict[str, object]:
    path = _safe_file(root, "manifest.json")
    with path.open("rb") as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise TraceError("manifest exceeds size limit")
    meta = json_object(raw.decode("utf-8"))
    if meta.get("status") != "complete":
        raise TraceError("recording is not complete")
    fields(
        meta,
        {
            "format_version",
            "status",
            "run_id",
            "schema_version",
            "core_version",
            "core_sha256",
            "profile_version",
            "profile_sha256",
            "package_versions",
            "configuration",
            "initial_state",
            "config_sha256",
            "git_commit",
            "git_dirty",
            "end_time_ns",
            "final_state",
            "semantic_sha256",
            "artifacts",
        },
        {"scenario", "live_model"},
    )
    if (
        integer(meta["format_version"], "format version") != 1
        or integer(meta["schema_version"], "schema version") != 1
    ):
        raise TraceError("unsupported trace schema")
    profile_from_version(meta["profile_version"])
    if "scenario" in meta:
        scenario = object_value(meta["scenario"], "scenario provenance")
        fields(
            scenario,
            {"id", "name", "description", "duration_ns", "profile_id", "seed", "source_sha256"},
        )
        for key in ("id", "name", "description"):
            _text(scenario[key], f"scenario {key}")
        integer(scenario["duration_ns"], "scenario duration")
        integer(scenario["seed"], "scenario seed")
        if (
            profile_version(_text(scenario["profile_id"], "scenario profile"))
            != meta["profile_version"]
        ):
            raise TraceError("scenario profile mismatch")
        _hash(scenario["source_sha256"])
    if "live_model" in meta:
        model = object_value(meta["live_model"], "live model provenance")
        fields(model, {"model_id", "model_sha256", "threshold", "feature_schema_version"})
        _text(model["model_id"], "model ID")
        _hash(model["model_sha256"])
        number(model["threshold"], "model threshold", minimum=0, maximum=1)
        integer(model["feature_schema_version"], "model feature schema version", minimum=1)
    _text(meta["run_id"], "run ID")
    _text(meta["core_version"], "core version")
    for name in ("core_sha256", "profile_sha256", "config_sha256", "semantic_sha256"):
        _hash(meta[name])
    integer(meta["end_time_ns"], "end time")
    configuration = object_value(meta["configuration"], "configuration")
    initial = object_value(meta["initial_state"], "initial state")
    object_value(meta["final_state"], "final state")
    packages = object_value(meta["package_versions"], "package versions")
    for key, value in packages.items():
        _text(value, f"package version {key}")
    if (
        sha256(
            canonical_bytes({"configuration": configuration, "initial_state": initial})
        ).hexdigest()
        != meta["config_sha256"]
    ):
        raise TraceError("configuration checksum mismatch")
    artifacts = object_value(meta["artifacts"], "artifacts")
    if set(artifacts) != set(ARTIFACTS):
        raise TraceError("unsupported artifact paths")
    total = 0
    for name in ARTIFACTS:
        stat = object_value(artifacts[name], "artifact metadata")
        fields(stat, {"bytes", "sha256", "records"})
        size = integer(stat["bytes"], "artifact size")
        integer(stat["records"], "record count")
        total += size
        if total > DEFAULT_MAX_BYTES:
            raise TraceError("trace exceeds import size limit")
        artifact = _safe_file(root, name)
        if artifact.stat().st_size != size:
            raise TraceError(f"artifact size mismatch: {name}")
        with artifact.open("rb") as stream:
            digest = sha256()
            while block := stream.read(65536):
                digest.update(block)
        if digest.hexdigest() != _hash(stat["sha256"]):
            raise TraceError(f"artifact checksum mismatch: {name}")
    if object_value(artifacts["profile.dbc"], "profile")["sha256"] != meta["profile_sha256"]:
        raise TraceError("profile checksum mismatch")
    return meta


def _artifact_records(root: Path, name: str) -> Iterator[dict[str, object]]:
    previous = 0
    with _safe_file(root, name).open("rb") as stream:
        while raw := stream.readline(MAX_LINE_BYTES + 1):
            if len(raw) > MAX_LINE_BYTES or not raw.endswith(b"\n"):
                raise TraceError("invalid or oversized JSONL line")
            record = json_object(raw.decode("utf-8"))
            seq = integer(record.get("event_seq"), "event_seq", minimum=1)
            if seq <= previous or _stream_name(record.get("event_kind")) != name:
                raise TraceError("event ordering or stream classification mismatch")
            previous = seq
            yield record


def _validate_event(record: dict[str, object], meta: dict[str, object]) -> None:
    base = {"run_id", "event_seq", "sim_time_ns", "event_kind", "schema_version", "profile_version"}
    kind = record.get("event_kind")
    network = meta["profile_version"] == "democar-0.2.0"
    if (
        record.get("run_id") != meta["run_id"]
        or record.get("profile_version") != meta["profile_version"]
        or integer(record.get("schema_version"), "schema version") != 1
    ):
        raise TraceError("event identity or schema mismatch")
    now = integer(record.get("sim_time_ns"), "sim_time_ns")
    if kind == "FramePublished":
        fields(
            record,
            base
            | {
                "frame_id",
                "source_ecu",
                "channel",
                "can_id",
                "dlc",
                "is_extended_id",
                "payload_hex",
                "created_at_ns",
            },
        )
        payload = _text(record["payload_hex"], "payload")
        raw = bytes.fromhex(payload)
        if (
            raw.hex() != payload
            or record["is_extended_id"] is not False
            or integer(record["created_at_ns"], "created time") != now
        ):
            raise TraceError("invalid frame encoding or creation time")
        CanFrame(
            integer(record["frame_id"], "frame_id", minimum=1),
            _text(record["source_ecu"], "source ECU"),
            _text(record["channel"], "channel"),
            integer(record["can_id"], "CAN ID"),
            integer(record["dlc"], "DLC"),
            raw,
            now,
        )
        if network:
            plan = PLAN_BY_ID.get(integer(record["can_id"], "CAN ID"))
            if plan is None or record["source_ecu"] != plan.source_ecu or record["dlc"] != plan.dlc:
                raise TraceError("frame does not match the network profile")
    elif kind in {"FrameReceived", "FrameDropped"}:
        fields(
            record,
            base
            | {"frame_id", "receiver_id"}
            | ({"received_at_ns"} if kind == "FrameReceived" else {"seed"}),
        )
        integer(record["frame_id"], "frame_id", minimum=1)
        if record["receiver_id"] not in (RECEIVERS if network else ("KOMBI",)):
            raise TraceError("unsupported receiver")
        if kind == "FrameReceived" and integer(record["received_at_ns"], "received time") != now:
            raise TraceError("delivery time mismatch")
        if kind == "FrameDropped":
            integer(record["seed"], "drop seed")
    elif kind == "InputApplied":
        command_kind = record.get("command_kind")
        parameter = command_parameter(command_kind)
        result_fields = (
            ({"request_sequence"} if network else {"accepted", "reason"})
            if command_kind == "set_gear"
            else set()
        )
        if command_kind == "set_ecu_publishing":
            result_fields.add("ecu_id")
        if command_kind in ("inject_fault", "clear_fault"):
            if not network or type(record.get("accepted")) is not bool:
                raise TraceError("invalid network fault command result")
            result_fields.add("accepted")
        fields(
            record,
            base | {"command_seq", "applied_at_ns", "command_kind", parameter} | result_fields,
        )
        if command_kind == "set_gear" and network:
            if integer(record["request_sequence"], "request sequence") > 65535:
                raise TraceError("invalid request sequence")
        if command_kind == "set_gear" and not network:
            accepted, reason = record["accepted"], record["reason"]
            if type(accepted) is not bool or (
                (accepted and reason is not None)
                or (not accepted and reason not in ("vehicle_moving", "brake_required"))
            ):
                raise TraceError("invalid gear request result")
        integer(record["command_seq"], "command_seq", minimum=1)
        if integer(record["applied_at_ns"], "applied time") != now:
            raise TraceError("command time mismatch")
        ScenarioCommand.from_record(record)
    elif kind == "PhysicalMotionUpdated":
        fields(record, base | {"physical_speed_kph"})
        motion = object_value(
            object_value(meta["configuration"], "configuration").get("motion"), "motion"
        )
        forward = number(motion.get("max_forward_kph"), "forward limit", minimum=0, maximum=300)
        reverse = number(motion.get("max_reverse_kph"), "reverse limit", minimum=0, maximum=60)
        number(record["physical_speed_kph"], "physical speed", minimum=-reverse, maximum=forward)
    elif kind == "EcuStatusChanged":
        fields(record, base | {"ecu_id", "status"} | ({"message_name"} if network else set()))
        if record["ecu_id"] not in (RECEIVERS if network else ("KOMBI",)) or _text(
            record["status"], "ECU status"
        ) not in {
            "FRESH",
            "STALE",
            "TIMEOUT",
        }:
            raise TraceError("invalid ECU status")
        if network:
            plan = PLAN_BY_NAME.get(_text(record["message_name"], "message name"))
            if plan is None or record["ecu_id"] not in plan.receivers:
                raise TraceError("status is not a subscribed message")
    elif kind == "GearShiftEvaluated" and network:
        fields(record, base | {"request_sequence", "requested_gear", "gear", "accepted", "reason"})
        if integer(record["request_sequence"], "request sequence") > 65535:
            raise TraceError("invalid request sequence")
        if record["gear"] not in GEARS or record["requested_gear"] not in GEARS:
            raise TraceError("invalid gear result")
        if (
            type(record["accepted"]) is not bool
            or record["reason"] not in tuple(SHIFT_REASONS)
            or (record["accepted"] != (record["reason"] is None))
        ):
            raise TraceError("invalid gear result")
    elif kind in {"FaultActivated", "FaultCleared"}:
        fields(
            record,
            base
            | {"fault_id", "type", "target_ecu", "target_message_id", "start_at_ns", "seed"}
            | ({"end_at_ns"} if kind == "FaultCleared" else set()),
            {"fault_json"} if network else set(),
        )
        _text(record["fault_id"], "fault ID")
        if "fault_json" in record:
            spec = FaultSpec.from_dict(json_object(_text(record["fault_json"], "fault JSON")))
            if any(
                record[key] != spec.as_dict()[key]
                for key in ("fault_id", "type", "target_ecu", "target_message_id", "seed")
            ):
                raise TraceError("fault metadata does not match its specification")
        elif (
            record["type"] != "ecu_offline"
            or record["target_ecu"] not in (ECU_IDS if network else ("engine",))
            or (
                record["target_message_id"] is not None
                if network
                else integer(record["target_message_id"], "target message") != 0x200
            )
        ):
            raise TraceError("unsupported fault")
        start = integer(record["start_at_ns"], "fault start")
        integer(record["seed"], "fault seed")
        if start > now or (kind == "FaultActivated" and start != now):
            raise TraceError("invalid fault interval")
        if kind == "FaultCleared" and integer(record["end_at_ns"], "fault end") != now:
            raise TraceError("invalid fault end")
    else:
        raise TraceError("unsupported event kind")


def _ordered_events(root: Path) -> Iterator[dict[str, object]]:
    yield from merge(
        *(_artifact_records(root, name) for name in STREAMS),
        key=lambda record: integer(record["event_seq"], "event_seq", minimum=1),
    )


def verify_trace(root: Path) -> dict[str, object]:
    try:
        meta = _read_meta(root)
        digest = sha256()
        previous_seq = previous_time = frame_seq = command_seq = 0
        pending: dict[tuple[int, str], int] = {}
        pending_counts = dict.fromkeys(RECEIVERS, 0)
        network = meta["profile_version"] == "democar-0.2.0"
        counts = dict.fromkeys(STREAMS, 0)
        for record in _ordered_events(root):
            _validate_event(record, meta)
            seq = integer(record["event_seq"], "event_seq", minimum=1)
            now = integer(record["sim_time_ns"], "sim_time_ns")
            if (
                seq != previous_seq + 1
                or now < previous_time
                or now > integer(meta["end_time_ns"], "end time")
            ):
                raise TraceError("canonical event ordering mismatch")
            previous_seq, previous_time = seq, now
            kind = record["event_kind"]
            if kind == "FramePublished":
                current = integer(record["frame_id"], "frame_id")
                # FramePublished precedes FrameDropped. One provisional frame may
                # coexist with 256 queued deliveries without occupying bus space.
                receivers = (
                    PLAN_BY_ID[integer(record["can_id"], "CAN ID")].receivers
                    if network
                    else ("KOMBI",)
                )
                if current != frame_seq + 1 or any(
                    pending_counts[name] >= 257 for name in receivers
                ):
                    raise TraceError("invalid frame order or unbounded pending deliveries")
                frame_seq = current
                for name in receivers:
                    pending[(current, name)] = now
                    pending_counts[name] += 1
            elif kind in {"FrameReceived", "FrameDropped"}:
                current = integer(record["frame_id"], "frame_id")
                receiver = _text(record["receiver_id"], "receiver")
                if (current, receiver) not in pending or pending.pop((current, receiver)) > now:
                    raise TraceError("delivery or drop references an unknown frame")
                pending_counts[receiver] -= 1
            elif kind == "InputApplied":
                if integer(record["command_seq"], "command_seq") != command_seq + 1:
                    raise TraceError("command order mismatch")
                command_seq += 1
            counts[_stream_name(kind)] += 1
            digest.update(_semantic(record))
        artifacts = object_value(meta["artifacts"], "artifacts")
        if any(
            counts[name] != object_value(artifacts[name], "artifact")["records"] for name in STREAMS
        ):
            raise TraceError("record count mismatch")
        if digest.hexdigest() != meta["semantic_sha256"]:
            raise TraceError("semantic checksum mismatch")
        return meta
    except (ValueError, OSError) as exc:
        raise TraceError(str(exc)) from exc


def playback(root: Path) -> Iterator[dict[str, object]]:
    """Verify and display saved events; no Simulation is constructed."""
    verify_trace(root)
    yield from _ordered_events(root)


def rerun(root: Path) -> dict[str, object]:
    meta = verify_trace(root)
    if meta["core_version"] != CORE_VERSION or meta["core_sha256"] != _core_hash():
        raise TraceError("current core is incompatible with the recording")
    if (
        meta["profile_sha256"]
        != sha256(_profile_bytes(profile_from_version(meta["profile_version"]))).hexdigest()
        or meta["package_versions"] != _packages()
    ):
        raise TraceError("current profile or runtime versions are incompatible")
    config = object_value(meta["configuration"], "configuration")
    digest = sha256()
    count = 0

    def collect(event: RunEvent) -> None:
        nonlocal count
        digest.update(_semantic(event.as_dict()))
        count += 1

    with Simulation(
        seed=integer(config.get("seed"), "seed"),
        profile_id=profile_from_version(meta["profile_version"]),
        delivery_delay_ns=integer(config.get("delivery_delay_ns"), "delay"),
        drop_probability=number(
            config.get("drop_probability"), "drop probability", minimum=0, maximum=1
        ),
        on_event=collect,
    ) as sim:
        if config != sim.configuration() or meta["initial_state"] != sim.state():
            raise TraceError("unsupported configuration or initial state")
        for time_ns, group in groupby(
            _artifact_records(root, "commands.jsonl"),
            key=lambda r: integer(r["applied_at_ns"], "applied time"),
        ):
            for index, record in enumerate(group):
                if index >= MAX_COMMANDS:
                    raise TraceError("too many simultaneous commands")
                ScenarioCommand.from_record(record).schedule(sim)
            sim.advance_to(time_ns)
        sim.advance_to(integer(meta["end_time_ns"], "end time"))
        saved_state = object_value(meta["final_state"], "final state")
        if saved_state.get("paused") is True:
            sim.pause()
        if sim.state() != saved_state or digest.hexdigest() != meta["semantic_sha256"]:
            raise TraceError("rerun differs from the recorded events or final state")
    return {"matches": True, "semantic_sha256": digest.hexdigest(), "event_count": count}
