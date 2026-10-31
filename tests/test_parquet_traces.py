"""Parquet round trips and observer datasets exercise real disk artifacts."""

import json
import subprocess
import sys
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from autocan.scenarios import Scenario
from autocan.traces import TraceError, playback, record_scenario, rerun, verify_trace
from autocan.validation import integer


@pytest.mark.parametrize("scenario", ["rpm_spike", "timing_jitter", "abs_missing_frames"])
def test_parquet_roundtrip_preserves_all_events_and_fresh_rerun(
    tmp_path: Path, scenario: str
) -> None:
    from autocan.parquet import export_trace, import_trace

    source, bundle, restored = (tmp_path / name for name in ("source", "bundle", "restored"))
    spec = replace(Scenario.load(Path("scenarios") / f"{scenario}.json"), duration_ns=2_000_000_000)
    original = record_scenario(spec, source)
    exported = export_trace(source, bundle)
    assert exported["trace_manifest"] == original
    frames = pq.ParquetFile(bundle / "frames.parquet")
    assert frames.schema_arrow.field("frame_id").type == pa.int64()
    assert frames.schema_arrow.field("payload").type == pa.binary()
    assert frames.num_row_groups > 1  # bounded row groups, not a whole-run table
    import_trace(bundle, restored)
    assert list(playback(restored)) == list(playback(source))
    assert verify_trace(restored)["semantic_sha256"] == original["semantic_sha256"]
    assert rerun(restored)["matches"] is True


def test_historical_jsonl_can_roundtrip_without_requiring_current_core(tmp_path: Path) -> None:
    from autocan.parquet import export_trace, import_trace

    source = Path("tests/fixtures/v0_1_engine_offline")
    export_trace(source, tmp_path / "bundle")
    import_trace(tmp_path / "bundle", tmp_path / "restored")
    assert list(playback(tmp_path / "restored")) == list(playback(source))
    with pytest.raises(TraceError, match="incompatible"):
        rerun(tmp_path / "restored")


def test_observer_dataset_uses_receipts_and_excludes_hidden_provenance(tmp_path: Path) -> None:
    from autocan.parquet import export_observer

    source = tmp_path / "run"
    record_scenario(Scenario.load(Path("scenarios/timing_jitter.json")), source)
    meta = export_observer(source, tmp_path / "dataset", receiver_id="observer")
    rows = pq.read_table(tmp_path / "dataset/observations.parquet").to_pylist()
    assert set(rows[0]) == {
        "received_at_ns",
        "channel",
        "can_id",
        "is_extended_id",
        "dlc",
        "payload",
    }
    events = list(playback(source))
    published = {e["frame_id"]: e for e in events if e["event_kind"] == "FramePublished"}
    receipts = [
        e for e in events if e["event_kind"] == "FrameReceived" and e["receiver_id"] == "observer"
    ]
    assert len(rows) == len(receipts)
    assert any(
        integer(e["received_at_ns"], "receipt")
        > integer(published[e["frame_id"]]["created_at_ns"], "creation")
        for e in receipts
    )
    for row, receipt in zip(rows, receipts, strict=True):
        frame = published[receipt["frame_id"]]
        assert row["received_at_ns"] == receipt["received_at_ns"]
        assert row["payload"] == bytes.fromhex(str(frame["payload_hex"]))
        assert row["can_id"] == frame["can_id"]
    labels = pq.read_table(tmp_path / "dataset/labels/faults.parquet").to_pylist()
    assert labels
    assert all(
        json.loads(row["event_json"])["event_kind"] in {"FaultActivated", "FaultCleared"}
        for row in labels
    )
    assert meta["receiver_id"] == "observer"
    export_observer(source, tmp_path / "kombi", receiver_id="KOMBI")
    kombi = pq.read_table(tmp_path / "kombi/observations.parquet").to_pylist()
    assert len(kombi) < len(rows)  # the selected subscription changes the actual data
    with pytest.raises(TraceError, match="receiver"):
        export_observer(source, tmp_path / "invalid", receiver_id="invented")
    assert not (tmp_path / "invalid").exists()


def test_exports_never_overwrite_and_quota_failure_does_not_publish(tmp_path: Path) -> None:
    from autocan.parquet import export_observer, export_trace

    source = tmp_path / "source"
    record_scenario(Scenario(duration_ns=200_000_000), source)
    for index, operation in enumerate((export_trace, export_observer)):
        target = tmp_path / f"export-{index}"
        operation(source, target)
        manifest = (target / "manifest.json").read_bytes()
        with pytest.raises(FileExistsError):
            operation(source, target)
        assert (target / "manifest.json").read_bytes() == manifest
        failed = tmp_path / f"failed-{index}"
        with pytest.raises(TraceError, match="quota"):
            operation(source, failed, max_bytes=4096)
        assert not failed.exists()


def test_corruption_and_symlinks_are_rejected_before_publishing_import(tmp_path: Path) -> None:
    from autocan.parquet import export_trace, import_trace

    source, bundle, target = (tmp_path / name for name in ("source", "bundle", "target"))
    record_scenario(Scenario(duration_ns=40_000_000), source)
    export_trace(source, bundle)
    frames = bundle / "frames.parquet"
    original = frames.read_bytes()
    frames.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    with pytest.raises(TraceError, match="checksum"):
        import_trace(bundle, target)
    assert not target.exists()
    frames.unlink()
    outside = tmp_path / "outside.parquet"
    outside.write_bytes(original)
    frames.symlink_to(outside)
    with pytest.raises(TraceError, match="symlink"):
        import_trace(bundle, target)
    assert not target.exists()


def test_changed_parquet_with_updated_file_hash_still_fails_semantic_verification(
    tmp_path: Path,
) -> None:
    from autocan.parquet import export_trace, import_trace

    source, bundle, target = (tmp_path / name for name in ("source", "bundle", "target"))
    record_scenario(Scenario(duration_ns=40_000_000), source)
    export_trace(source, bundle)
    path = bundle / "frames.parquet"
    table = pq.read_table(path)
    rows = table.to_pylist()
    rows[0]["payload"] = b"\x01" * rows[0]["dlc"]
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    artifact = manifest["artifacts"]["frames.parquet"]
    artifact.update(bytes=path.stat().st_size, sha256=sha256(path.read_bytes()).hexdigest())
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(TraceError, match="checksum"):
        import_trace(bundle, target)
    assert not target.exists()


def test_cli_exports_imports_and_builds_observer_dataset(tmp_path: Path) -> None:
    source = tmp_path / "source"
    record_scenario(Scenario(duration_ns=60_000_000), source)
    bundle, restored, dataset = (tmp_path / name for name in ("bundle", "restored", "dataset"))
    for args in (
        ["trace-export", str(source), "--output", str(bundle)],
        ["trace-import", str(bundle), "--output", str(restored)],
        ["dataset", str(restored), "--receiver", "KOMBI", "--output", str(dataset)],
        ["rerun", str(restored)],
    ):
        result = subprocess.run(
            [sys.executable, "-m", "autocan", *args], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)
    assert pq.read_table(dataset / "observations.parquet").num_rows == 4


@pytest.mark.parametrize("drop_probability,delay_ns", [(1.0, 0), (0.0, 100_000_000)])
def test_no_receipts_produce_typed_empty_observations_and_lossless_exchange(
    tmp_path: Path, drop_probability: float, delay_ns: int
) -> None:
    from autocan.parquet import export_observer, export_trace, import_trace

    source = tmp_path / "source"
    record_scenario(
        Scenario(
            duration_ns=60_000_000, delivery_delay_ns=delay_ns, drop_probability=drop_probability
        ),
        source,
    )
    export_observer(source, tmp_path / "dataset")
    table = pq.read_table(tmp_path / "dataset/observations.parquet")
    assert table.num_rows == 0
    assert table.schema.field("received_at_ns").type == pa.int64()
    assert pq.read_table(tmp_path / "dataset/labels/faults.parquet").num_rows == 0
    export_trace(source, tmp_path / "bundle")
    import_trace(tmp_path / "bundle", tmp_path / "restored")
    assert list(playback(tmp_path / "restored")) == list(playback(source))
    assert rerun(tmp_path / "restored")["matches"] is True


def test_import_quota_and_existing_destination_preserve_files(tmp_path: Path) -> None:
    from autocan.parquet import export_trace, import_trace

    source, bundle, target = (tmp_path / name for name in ("source", "bundle", "target"))
    record_scenario(Scenario(duration_ns=200_000_000), source)
    export_trace(source, bundle)
    with pytest.raises(TraceError, match="quota"):
        import_trace(bundle, target, max_bytes=4096)
    assert not target.exists()
    target.mkdir()
    (target / "keep").write_text("original")
    with pytest.raises(TraceError):
        import_trace(bundle, target)
    assert (target / "keep").read_text() == "original"
    assert not (target / "manifest.json").exists()
