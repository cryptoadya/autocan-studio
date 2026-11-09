"""Small synthetic runs exercise storage integrity, separation and real reruns."""

import json
from hashlib import sha256
from pathlib import Path

import pytest


def offline_scenario() -> dict[str, object]:
    return {
        "schema_version": 1,
        "duration_ns": 600_000_000,
        "seed": 42,
        "commands": [
            {"at_ns": 0, "kind": "set_throttle", "percent": 50},
            {"at_ns": 100_000_000, "kind": "set_engine_publishing", "enabled": False},
            {"at_ns": 400_000_000, "kind": "set_engine_publishing", "enabled": True},
        ],
    }


def test_record_playback_rerun_preserves_events_and_separates_ground_truth(tmp_path: Path) -> None:
    from autocan.scenarios import Scenario
    from autocan.traces import playback, record_scenario, rerun, verify_trace

    target = tmp_path / "run"
    manifest = record_scenario(Scenario.from_dict(offline_scenario()), target)
    assert manifest["status"] == "complete"
    assert verify_trace(target)["semantic_sha256"] == manifest["semantic_sha256"]
    events = list(playback(target))
    statuses = [
        (e["sim_time_ns"], e["status"]) for e in events if e["event_kind"] == "EcuStatusChanged"
    ]
    assert statuses == [
        (0, "FRESH"),
        (140_000_000, "STALE"),
        (280_000_000, "TIMEOUT"),
        (400_000_000, "FRESH"),
    ]
    assert all(
        e["event_kind"] not in {"FaultActivated", "FaultCleared", "InputApplied"}
        for e in map(json.loads, (target / "events.jsonl").read_text().splitlines())
    )
    assert len((target / "commands.jsonl").read_text().splitlines()) == 3
    assert len((target / "faults.jsonl").read_text().splitlines()) == 2
    assert rerun(target)["matches"] is True
    assert (target / "profile.dbc").is_file()


def test_drop_and_delay_runs_reproduce_with_seeded_transport(tmp_path: Path) -> None:
    from autocan.scenarios import Scenario
    from autocan.traces import record_scenario, rerun

    spec = offline_scenario()
    spec["transport"] = {"delay_ns": 30_000_000, "drop_probability": 0.4}
    record_scenario(Scenario.from_dict(spec), tmp_path / "run")
    assert rerun(tmp_path / "run")["matches"] is True


def test_dropped_publication_at_full_delivery_queue_remains_loadable(tmp_path: Path) -> None:
    from autocan.scenarios import Scenario
    from autocan.traces import record_scenario, rerun, verify_trace

    scenario = Scenario(
        duration_ns=9_660_000_000,
        seed=1,
        delivery_delay_ns=60_000_000_000,
        drop_probability=0.5,
    )
    target = tmp_path / "run"
    meta = record_scenario(scenario, target)
    assert meta["status"] == "complete"
    assert verify_trace(target)["status"] == "complete"
    assert rerun(target)["matches"] is True


def test_rerun_uses_applied_commands_without_requiring_original_scenario_file(
    tmp_path: Path,
) -> None:
    from autocan.scenarios import Scenario
    from autocan.traces import record_scenario, rerun

    record_scenario(Scenario.from_dict(offline_scenario()), tmp_path / "run")
    assert rerun(tmp_path / "run")["matches"] is True
    assert not (tmp_path / "run" / "scenario.json").exists()


def test_checksum_failure_is_rejected_before_rerun(tmp_path: Path) -> None:
    from autocan.scenarios import Scenario
    from autocan.traces import TraceError, record_scenario, rerun

    target = tmp_path / "run"
    record_scenario(Scenario.from_dict(offline_scenario()), target)
    with (target / "events.jsonl").open("ab") as stream:
        stream.write(b"{}\n")
    with pytest.raises(TraceError, match="checksum|size"):
        rerun(target)


def test_recording_never_overwrites_an_existing_run(tmp_path: Path) -> None:
    from autocan.scenarios import Scenario
    from autocan.traces import record_scenario

    scenario = Scenario.from_dict(offline_scenario())
    target = tmp_path / "run"
    record_scenario(scenario, target)
    original = (target / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        record_scenario(scenario, target)
    assert (target / "manifest.json").read_bytes() == original


def test_quota_failure_leaves_failed_manifest_and_cannot_be_played(tmp_path: Path) -> None:
    from autocan.scenarios import Scenario
    from autocan.traces import TraceError, playback, record_scenario

    target = tmp_path / "run"
    with pytest.raises(TraceError, match="quota"):
        record_scenario(Scenario.from_dict(offline_scenario()), target, max_bytes=4096)
    assert json.loads((target / "manifest.json").read_text())["status"] == "failed"
    with pytest.raises(TraceError, match="complete"):
        list(playback(target))


def test_artifact_paths_are_fixed_and_symlinks_are_rejected(tmp_path: Path) -> None:
    from autocan.scenarios import Scenario
    from autocan.traces import TraceError, record_scenario, verify_trace

    target = tmp_path / "run"
    record_scenario(Scenario.from_dict(offline_scenario()), target)
    original = (target / "events.jsonl").read_bytes()
    (tmp_path / "outside.jsonl").write_bytes(original)
    (target / "events.jsonl").unlink()
    (target / "events.jsonl").symlink_to(tmp_path / "outside.jsonl")
    with pytest.raises(TraceError, match="symlink"):
        verify_trace(target)


@pytest.mark.parametrize("previous_version", ["0.1.0", "1.0.0"])
def test_rerun_accepts_an_earlier_package_with_identical_core_and_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, previous_version: str
) -> None:
    from autocan import traces
    from autocan.scenarios import Scenario

    target = tmp_path / "run"
    traces.record_scenario(Scenario.from_dict(offline_scenario()), target)
    manifest = target / "manifest.json"
    meta = json.loads(manifest.read_text())
    meta["package_versions"]["autocan-studio"] = previous_version
    manifest.write_text(json.dumps(meta))
    packages = {**traces._packages(), "autocan-studio": "2.0.0"}
    monkeypatch.setattr(traces, "_packages", lambda: packages)
    assert traces.rerun(target)["matches"] is True

    meta["package_versions"]["python-can"] = "0.0"
    manifest.write_text(json.dumps(meta))
    with pytest.raises(traces.TraceError, match="runtime versions are incompatible"):
        traces.rerun(target)


def test_incompatible_core_rejects_rerun_but_allows_saved_playback(tmp_path: Path) -> None:
    from autocan.scenarios import Scenario
    from autocan.traces import TraceError, playback, record_scenario, rerun

    target = tmp_path / "run"
    record_scenario(Scenario.from_dict(offline_scenario()), target)
    meta = json.loads((target / "manifest.json").read_text())
    meta["core_sha256"] = "0" * 64
    (target / "manifest.json").write_text(json.dumps(meta))
    assert len(list(playback(target))) > 0
    with pytest.raises(TraceError, match="core"):
        rerun(target)


def test_malformed_event_kind_is_a_schema_error_even_with_updated_file_checksum(
    tmp_path: Path,
) -> None:
    from autocan.scenarios import Scenario
    from autocan.traces import TraceError, record_scenario, verify_trace

    target = tmp_path / "run"
    record_scenario(Scenario.from_dict(offline_scenario()), target)
    lines = (target / "events.jsonl").read_text().splitlines()
    record = json.loads(lines[0])
    record["event_kind"] = []
    lines[0] = json.dumps(record)
    raw = ("\n".join(lines) + "\n").encode()
    (target / "events.jsonl").write_bytes(raw)
    meta = json.loads((target / "manifest.json").read_text())
    meta["artifacts"]["events.jsonl"].update(bytes=len(raw), sha256=sha256(raw).hexdigest())
    (target / "manifest.json").write_text(json.dumps(meta))
    with pytest.raises(TraceError):
        verify_trace(target)


def test_recorder_cannot_finalize_after_reset_or_without_initial_events(tmp_path: Path) -> None:
    from autocan.simulation import Simulation
    from autocan.traces import TraceError, TraceRecorder

    with pytest.raises(TraceError):
        with TraceRecorder(tmp_path / "empty") as recorder:
            with Simulation(on_event=recorder.write) as sim:
                recorder.begin(sim)
                recorder.finish(sim.state())
    with pytest.raises(TraceError):
        with TraceRecorder(tmp_path / "reset") as recorder:
            with Simulation(on_event=recorder.write) as sim:
                recorder.begin(sim)
                sim.advance_to(0)
                sim.reset()
                recorder.finish(sim.state())


@pytest.mark.parametrize(
    "change",
    [
        {"duration_ns": True},
        {"unknown": "field"},
        {"commands": [{"at_ns": 1, "kind": "set_throttle", "percent": 50}]},
        {"commands": [{"at_ns": 0, "kind": "eval", "code": "print(1)"}]},
        {"commands": [{"at_ns": 0, "kind": "set_engine_publishing", "enabled": 0}]},
        {"transport": {"drop_probability": float("nan")}},
    ],
)
def test_scenario_validation_rejects_unsupported_or_invalid_inputs(
    change: dict[str, object],
) -> None:
    from autocan.scenarios import Scenario

    with pytest.raises(ValueError):
        Scenario.from_dict(offline_scenario() | change)
