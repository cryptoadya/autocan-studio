"""Bounded Parquet exchange and causal, single-receiver observation datasets.

JSONL remains the recording/replay format. Exchange bundles preserve every event
and the original manifest; importing must pass the original trace verifier.
"""

from collections.abc import Generator, Iterator
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from hashlib import sha256
from heapq import merge
from itertools import zip_longest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq

from autocan.dbc import profile_bytes, profile_from_version
from autocan.events import CanObservation
from autocan.network_profile import PLAN_BY_ID, RECEIVERS
from autocan.traces import (
    DEFAULT_MAX_BYTES,
    MAX_LINE_BYTES,
    MAX_MANIFEST_BYTES,
    STREAMS,
    TraceError,
    _ordered_events,
    _safe_file,
    _semantic,
    _stream_name,
    verify_trace,
)
from autocan.validation import canonical_bytes, fields, integer, json_object, object_value

ROW_GROUP_ROWS = 256
MAX_DECODED_BYTES = 4 * DEFAULT_MAX_BYTES
MAX_BUNDLE_MANIFEST_BYTES = 2 * MAX_MANIFEST_BYTES


def _schema(columns: list[tuple[str, pa.DataType]]) -> pa.Schema:
    return pa.schema([pa.field(name, dtype, nullable=False) for name, dtype in columns])


FRAME_SCHEMA = _schema(
    [
        ("event_seq", pa.int64()),
        ("sim_time_ns", pa.int64()),
        ("frame_id", pa.int64()),
        ("source_ecu", pa.string()),
        ("channel", pa.string()),
        ("can_id", pa.int64()),
        ("dlc", pa.int64()),
        ("is_extended_id", pa.bool_()),
        ("payload", pa.binary()),
        ("created_at_ns", pa.int64()),
    ]
)
DELIVERY_SCHEMA = _schema(
    [
        ("event_seq", pa.int64()),
        ("sim_time_ns", pa.int64()),
        ("frame_id", pa.int64()),
        ("receiver_id", pa.string()),
        ("received_at_ns", pa.int64()),
    ]
)
EVENT_SCHEMA = _schema([("event_seq", pa.int64()), ("event_json", pa.string())])
OBSERVATION_SCHEMA = _schema(
    [
        ("received_at_ns", pa.int64()),
        ("channel", pa.string()),
        ("can_id", pa.int64()),
        ("is_extended_id", pa.bool_()),
        ("dlc", pa.int64()),
        ("payload", pa.binary()),
    ]
)
TABLES = {
    "frames.parquet": FRAME_SCHEMA,
    "deliveries.parquet": DELIVERY_SCHEMA,
    "events.parquet": EVENT_SCHEMA,
    "commands.parquet": EVENT_SCHEMA,
    "labels/faults.parquet": EVENT_SCHEMA,
}
OBSERVER_TABLES = {
    "observations.parquet": OBSERVATION_SCHEMA,
    "labels/faults.parquet": EVENT_SCHEMA,
}


def _quota(max_bytes: int) -> None:
    integer(max_bytes, "max_bytes", minimum=4096)
    if max_bytes > DEFAULT_MAX_BYTES:
        raise TraceError("quota exceeds the supported import limit")


@contextmanager
def _new_output(target: Path) -> Iterator[Path]:
    """Stage privately, then exclusively reserve the destination; manifest last."""
    if target.exists() or target.is_symlink():
        raise FileExistsError(str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".autocan-", dir=target.parent) as temporary:
        staging = Path(temporary)
        yield staging
        target.mkdir(exist_ok=False)
        for path in sorted(staging.iterdir(), key=lambda p: p.name == "manifest.json"):
            path.rename(target / path.name)


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(65536):
            digest.update(block)
    return digest.hexdigest()


class _TableWriter:
    def __init__(self, root: Path, name: str, schema: pa.Schema, max_bytes: int) -> None:
        self.root, self.path, self.schema, self.limit = root, root / name, schema, max_bytes
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Keep generated bundles simple, with small bounded groups.
        self.writer = pq.ParquetWriter(
            str(self.path),
            schema,
            compression="NONE",
            use_dictionary=False,
            write_statistics=False,
            write_page_checksum=True,
        )
        self.rows: list[dict[str, object]] = []
        self.count = 0
        self.digest = sha256()

    def write(self, row: dict[str, object]) -> None:
        self.rows.append(row)
        self.count += 1
        # Binary payloads need a canonical JSON representation for semantic hashing.
        self.digest.update(
            canonical_bytes(
                {
                    key: value.hex() if isinstance(value, bytes) else value
                    for key, value in row.items()
                }
            )
            + b"\n"
        )
        if len(self.rows) >= ROW_GROUP_ROWS:
            self.flush()

    def flush(self) -> None:
        if self.rows:
            table = pa.Table.from_pylist(self.rows, schema=self.schema)
            self.writer.write_table(table, row_group_size=ROW_GROUP_ROWS)
            self.rows.clear()
            if sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file()) > self.limit:
                raise TraceError("Parquet export quota exceeded")

    def close(self) -> None:
        try:
            self.flush()
        finally:
            self.writer.close()


def _finish(
    root: Path, meta: dict[str, object], writers: dict[str, _TableWriter], max_bytes: int
) -> dict[str, object]:
    artifacts: dict[str, object] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        name = path.relative_to(root).as_posix()
        artifacts[name] = {
            "bytes": path.stat().st_size,
            "sha256": _file_hash(path),
            "records": writers[name].count if name in writers else 0,
            "semantic_sha256": writers[name].digest.hexdigest() if name in writers else None,
        }
    meta["artifacts"] = artifacts
    raw = canonical_bytes(meta) + b"\n"
    if len(raw) > MAX_BUNDLE_MANIFEST_BYTES:
        raise TraceError("bundle manifest exceeds size limit")
    if sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) + len(raw) > max_bytes:
        raise TraceError("Parquet export quota exceeded")
    (root / "manifest.json").write_bytes(raw)
    return meta


def _event_table(record: dict[str, object]) -> str:
    kind = record["event_kind"]
    if kind == "FramePublished":
        return "frames.parquet"
    if kind == "FrameReceived":
        return "deliveries.parquet"
    stream = _stream_name(kind)
    return (
        "commands.parquet"
        if stream == "commands.jsonl"
        else "labels/faults.parquet"
        if stream == "faults.jsonl"
        else "events.parquet"
    )


def _json_row(record: dict[str, object]) -> dict[str, object]:
    return {"event_seq": record["event_seq"], "event_json": canonical_bytes(record).decode()}


def export_trace(
    source: Path, target: Path, *, max_bytes: int = DEFAULT_MAX_BYTES
) -> dict[str, object]:
    """Export one verified run as typed frames/deliveries plus lossless other events."""
    _quota(max_bytes)
    trace_meta = verify_trace(source)
    with _new_output(target) as staging:
        (staging / "profile.dbc").write_bytes(_safe_file(source, "profile.dbc").read_bytes())
        writers: dict[str, _TableWriter] = {}
        digest = sha256()
        with ExitStack() as stack:
            for name, schema in TABLES.items():
                writer = writers[name] = _TableWriter(staging, name, schema, max_bytes)
                stack.callback(writer.close)
            for record in _ordered_events(source):
                name = _event_table(record)
                if name == "frames.parquet":
                    row = {key: record[key] for key in FRAME_SCHEMA.names if key != "payload"}
                    row["payload"] = bytes.fromhex(str(record["payload_hex"]))
                elif name == "deliveries.parquet":
                    row = {key: record[key] for key in DELIVERY_SCHEMA.names}
                else:
                    row = _json_row(record)
                writers[name].write(row)
                digest.update(_semantic(record))
        if digest.hexdigest() != trace_meta["semantic_sha256"]:
            raise TraceError("source semantic checksum changed during export")
        meta = _finish(
            staging,
            {
                "format": "autocan-parquet",
                "format_version": 1,
                "status": "complete",
                "trace_manifest": trace_meta,
            },
            writers,
            max_bytes,
        )
    return meta


def _read_export(source: Path, max_bytes: int, *, observer: bool = False) -> dict[str, object]:
    path = _safe_file(source, "manifest.json")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BUNDLE_MANIFEST_BYTES + 1)
    if len(raw) > MAX_BUNDLE_MANIFEST_BYTES:
        raise TraceError("bundle manifest exceeds size limit")
    meta = json_object(raw.decode())
    extra = {"receiver_id", "observation_schema_version"} if observer else set()
    fields(meta, {"format", "format_version", "status", "trace_manifest", "artifacts"} | extra)
    if (
        meta["format"] != ("autocan-observer-dataset" if observer else "autocan-parquet")
        or meta["status"] != "complete"
        or integer(meta["format_version"], "format version") != 1
        or (observer and integer(meta["observation_schema_version"], "observation schema") != 1)
    ):
        raise TraceError("unsupported or incomplete Parquet bundle")
    object_value(meta["trace_manifest"], "trace manifest")
    artifacts = object_value(meta["artifacts"], "artifacts")
    expected = set(OBSERVER_TABLES) if observer else TABLES.keys() | {"profile.dbc"}
    if artifacts.keys() != expected:
        raise TraceError("unsupported artifact paths")
    total = len(raw)
    for name, value in artifacts.items():
        info = object_value(value, "artifact")
        fields(info, {"bytes", "sha256", "records", "semantic_sha256"})
        size = integer(info["bytes"], "artifact size")
        integer(info["records"], "records")
        total += size
        if total > max_bytes:
            raise TraceError("Parquet import quota exceeded")
        # Check every path component, including the labels directory.
        for index in range(1, len(Path(name).parts) + 1):
            if source.joinpath(*Path(name).parts[:index]).is_symlink():
                raise TraceError(f"symlink artifact rejected: {name}")
        path = _safe_file(source, name)
        if path.stat().st_size != size or _file_hash(path) != info["sha256"]:
            raise TraceError(f"artifact checksum or size mismatch: {name}")
    return meta


def _rows(
    source: Path, name: str, info: dict[str, object]
) -> Generator[dict[str, object], None, None]:
    with pq.ParquetFile(
        str(_safe_file(source, name)),
        pre_buffer=False,
        thrift_string_size_limit=MAX_BUNDLE_MANIFEST_BYTES,
        thrift_container_size_limit=MAX_BUNDLE_MANIFEST_BYTES,
        page_checksum_verification=True,
        arrow_extensions_enabled=False,
    ) as parquet:
        schema = OBSERVATION_SCHEMA if name == "observations.parquet" else TABLES[name]
        if not parquet.schema_arrow.equals(schema, check_metadata=True):
            raise TraceError(f"unsupported table schema: {name}")
        if parquet.metadata.num_rows != info["records"]:
            raise TraceError("table record count mismatch")
        decoded = 0
        for i in range(parquet.num_row_groups):
            group = parquet.metadata.row_group(i)
            decoded += group.total_byte_size
            if (
                group.num_rows > ROW_GROUP_ROWS
                or group.total_byte_size > MAX_DECODED_BYTES
                or decoded > MAX_DECODED_BYTES
            ):
                raise TraceError("Parquet decoded size or row-group quota exceeded")
        digest = sha256()
        observation = name == "observations.parquet"
        previous = -1 if observation else 0
        for batch in parquet.iter_batches(batch_size=ROW_GROUP_ROWS, use_threads=False):
            for value in batch.to_pylist():
                row = object_value(value, "Parquet row")
                if any(value is None for value in row.values()):
                    raise TraceError("null values are not supported")
                key = "received_at_ns" if observation else "event_seq"
                seq = integer(row[key], key, minimum=0 if observation else 1)
                if seq < previous or (seq == previous and not observation):
                    raise TraceError("table event ordering mismatch")
                previous = seq
                digest.update(
                    canonical_bytes(
                        {
                            key: value.hex() if isinstance(value, bytes) else value
                            for key, value in row.items()
                        }
                    )
                    + b"\n"
                )
                yield row
        if digest.hexdigest() != info["semantic_sha256"]:
            raise TraceError(f"table semantic checksum mismatch: {name}")


def _events(
    source: Path, name: str, info: dict[str, object], trace_meta: dict[str, object]
) -> Generator[dict[str, object], None, None]:
    for row in _rows(source, name, info):
        if name in ("frames.parquet", "deliveries.parquet"):
            record = {
                "run_id": trace_meta["run_id"],
                "schema_version": trace_meta["schema_version"],
                "profile_version": trace_meta["profile_version"],
                "event_kind": "FramePublished" if name == "frames.parquet" else "FrameReceived",
                **row,
            }
            if name == "frames.parquet":
                payload = record.pop("payload")
                if not isinstance(payload, bytes):
                    raise TraceError("invalid binary payload")
                record["payload_hex"] = payload.hex()
        else:
            text = row["event_json"]
            if not isinstance(text, str) or len(text.encode()) > MAX_LINE_BYTES:
                raise TraceError("invalid or oversized event JSON")
            record = json_object(text)
            if record.get("event_seq") != row["event_seq"]:
                raise TraceError("event sequence mismatch")
        if _event_table(record) != name:
            raise TraceError("event table classification mismatch")
        yield record


def _bundle_events(
    source: Path, meta: dict[str, object]
) -> Generator[dict[str, object], None, None]:
    trace_meta = object_value(meta["trace_manifest"], "trace manifest")
    artifacts = object_value(meta["artifacts"], "artifacts")
    with ExitStack() as stack:
        streams = [
            stack.enter_context(
                closing(
                    _events(source, name, object_value(artifacts[name], "artifact"), trace_meta)
                )
            )
            for name in TABLES
        ]
        yield from merge(
            *streams,
            key=lambda record: integer(record.get("event_seq"), "event_seq", minimum=1),
        )


def import_trace(
    source: Path, target: Path, *, max_bytes: int = DEFAULT_MAX_BYTES
) -> dict[str, object]:
    """Restore canonical JSONL, then verify before exposing it for playback/rerun."""
    _quota(max_bytes)
    try:
        meta = _read_export(source, max_bytes)
        trace_meta = object_value(meta["trace_manifest"], "trace manifest")
        with _new_output(target) as staging:
            profile = _safe_file(source, "profile.dbc").read_bytes()
            (staging / "profile.dbc").write_bytes(profile)
            raw_meta = canonical_bytes(trace_meta) + b"\n"
            if len(raw_meta) > MAX_MANIFEST_BYTES:
                raise TraceError("trace manifest exceeds size limit")
            (staging / "manifest.json").write_bytes(raw_meta)
            size = len(profile)
            with ExitStack() as stack:
                streams = {
                    name: stack.enter_context((staging / name).open("xb")) for name in STREAMS
                }
                with closing(_bundle_events(source, meta)) as ordered:
                    for record in ordered:
                        raw = canonical_bytes(record) + b"\n"
                        size += len(raw)
                        if len(raw) > MAX_LINE_BYTES or size > max_bytes:
                            raise TraceError("restored trace quota exceeded")
                        streams[_stream_name(record.get("event_kind"))].write(raw)
            result = verify_trace(staging)
        return result
    except (ValueError, OSError, KeyError, pa.ArrowException) as exc:
        raise TraceError(str(exc)) from exc


def export_observer(
    source: Path,
    target: Path,
    *,
    receiver_id: str = "KOMBI",
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, object]:
    """Only wire fields and receipt time enter observations; truth is separate."""
    _quota(max_bytes)
    trace_meta = verify_trace(source)
    network = trace_meta["profile_version"] == "democar-0.2.0"
    if receiver_id not in (RECEIVERS if network else ("KOMBI",)):
        raise TraceError("unsupported receiver for this profile")
    with _new_output(target) as staging:
        writers: dict[str, _TableWriter] = {}
        with ExitStack() as stack:
            for name, schema in OBSERVER_TABLES.items():
                writer = writers[name] = _TableWriter(staging, name, schema, max_bytes)
                stack.callback(writer.close)
            for name, row in _observer_projection(source, trace_meta, receiver_id):
                writers[name].write(row)
        meta = _finish(
            staging,
            {
                "format": "autocan-observer-dataset",
                "format_version": 1,
                "status": "complete",
                "receiver_id": receiver_id,
                "trace_manifest": trace_meta,
                "observation_schema_version": 1,
            },
            writers,
            max_bytes,
        )
    return meta


@dataclass(frozen=True, slots=True)
class ObserverStream:
    profile_id: str
    receiver_id: str
    end_ns: int
    observations: Iterator[CanObservation]


@contextmanager
def open_observer(source: Path) -> Iterator[ObserverStream]:
    """Verify an M7 export, exposing wire observations and timing context only.

    Labels are checked for file integrity but never decoded or returned here.
    The iterator must be exhausted to finish its semantic checksum verification.
    """
    meta = _read_export(source, DEFAULT_MAX_BYTES, observer=True)
    trace = object_value(meta["trace_manifest"], "trace manifest")
    profile = profile_from_version(trace.get("profile_version"))
    if trace.get("profile_sha256") != sha256(profile_bytes(profile)).hexdigest():
        raise TraceError("observer profile checksum is incompatible")
    final = object_value(trace.get("final_state"), "final state")
    end = integer(final.get("sim_time_ns"), "run end")
    receiver = meta["receiver_id"]
    if not isinstance(receiver, str):
        raise TraceError("observer receiver must be a string")
    artifacts = object_value(meta["artifacts"], "artifacts")
    info = object_value(artifacts["observations.parquet"], "observations artifact")

    def observations() -> Generator[CanObservation, None, None]:
        with closing(_rows(source, "observations.parquet", info)) as rows:
            for row in rows:
                yield CanObservation(
                    channel=cast(str, row["channel"]),
                    can_id=integer(row["can_id"], "CAN ID"),
                    is_extended_id=cast(bool, row["is_extended_id"]),
                    dlc=integer(row["dlc"], "DLC"),
                    payload=cast(bytes, row["payload"]),
                    received_at_ns=integer(row["received_at_ns"], "receipt time"),
                )

    with closing(observations()) as stream:
        yield ObserverStream(profile, receiver, end, stream)


def _observer_projection(
    source: Path, trace_meta: dict[str, object], receiver_id: str
) -> Iterator[tuple[str, dict[str, object]]]:
    network = trace_meta["profile_version"] == "democar-0.2.0"
    pending: dict[int, dict[str, object]] = {}
    digest = sha256()
    for record in _ordered_events(source):
        digest.update(_semantic(record))
        kind = record["event_kind"]
        if kind == "FramePublished":
            routes = (
                PLAN_BY_ID[integer(record["can_id"], "CAN ID")].receivers if network else ("KOMBI",)
            )
            if receiver_id in routes:
                pending[integer(record["frame_id"], "frame_id")] = record
                if len(pending) > 257:
                    raise TraceError("unbounded pending observations")
        elif kind in {"FrameReceived", "FrameDropped"} and record["receiver_id"] == receiver_id:
            frame = pending.pop(integer(record["frame_id"], "frame_id"))
            if kind == "FrameReceived":
                yield (
                    "observations.parquet",
                    {
                        "received_at_ns": record["received_at_ns"],
                        **{
                            key: frame[key]
                            for key in ("channel", "can_id", "is_extended_id", "dlc")
                        },
                        "payload": bytes.fromhex(str(frame["payload_hex"])),
                    },
                )
        elif kind in {"FaultActivated", "FaultCleared"}:
            yield "labels/faults.parquet", _json_row(record)
    if digest.hexdigest() != trace_meta["semantic_sha256"]:
        raise TraceError("source semantic checksum changed during projection")


def verify_exports(
    trace: Path, bundle: Path, observer: Path
) -> tuple[dict[str, object], dict[str, object]]:
    """Check derived schemas/hashes and compare every row with its verified source."""
    try:
        trace_meta = verify_trace(trace)
        bundle_meta = _read_export(bundle, DEFAULT_MAX_BYTES)
        observed = _read_export(observer, DEFAULT_MAX_BYTES, observer=True)
        if bundle_meta["trace_manifest"] != trace_meta or observed["trace_manifest"] != trace_meta:
            raise TraceError("derived source provenance mismatch")
        artifacts = object_value(bundle_meta["artifacts"], "artifacts")
        if (
            object_value(artifacts["profile.dbc"], "profile")["sha256"]
            != trace_meta["profile_sha256"]
        ):
            raise TraceError("derived source profile mismatch")
        receiver = observed["receiver_id"]
        allowed = RECEIVERS if trace_meta["profile_version"] == "democar-0.2.0" else ("KOMBI",)
        if not isinstance(receiver, str) or receiver not in allowed:
            raise TraceError("unsupported receiver for this profile")
        with closing(_bundle_events(bundle, bundle_meta)) as actual:
            with closing(
                cast(Generator[dict[str, object], None, None], _ordered_events(trace))
            ) as expected:
                for saved, original in zip_longest(actual, expected):
                    if saved != original:
                        raise TraceError("Parquet source event projection mismatch")
        artifacts = object_value(observed["artifacts"], "artifacts")
        with ExitStack() as stack:
            rows = {
                name: stack.enter_context(
                    closing(_rows(observer, name, object_value(artifacts[name], "artifact")))
                )
                for name in OBSERVER_TABLES
            }
            for name, expected_row in _observer_projection(trace, trace_meta, receiver):
                if next(rows[name], None) != expected_row:
                    raise TraceError("observer source projection mismatch")
            if any(next(iterator, None) is not None for iterator in rows.values()):
                raise TraceError("observer source projection has extra rows")
        return trace_meta, observed
    except (ValueError, OSError, KeyError, pa.ArrowException) as exc:
        raise TraceError(str(exc)) from exc
