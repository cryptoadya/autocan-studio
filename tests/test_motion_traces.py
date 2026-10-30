"""Recorded model speed must replay without physics and rerun with shift results."""

import json
from hashlib import sha256
from pathlib import Path

import pytest

from autocan.api.runtime import saved_view
from autocan.scenarios import Scenario
from autocan.traces import TraceError, playback, record_scenario, rerun, verify_trace


def motion_scenario() -> Scenario:
    return Scenario.from_dict(
        {
            "schema_version": 1,
            "duration_ns": 1_200_000_000,
            "seed": 42,
            "commands": [
                {"at_ns": 0, "kind": "set_brake", "percent": 100},
                {"at_ns": 0, "kind": "set_gear", "gear": "D"},
                {"at_ns": 0, "kind": "set_brake", "percent": 0},
                {"at_ns": 0, "kind": "set_throttle", "percent": 100},
                {"at_ns": 1_010_000_000, "kind": "set_gear", "gear": "R"},
                {"at_ns": 1_100_000_000, "kind": "set_brake", "percent": 100},
            ],
        }
    )


def test_motion_records_rejected_shifts_and_replays_physical_speed(tmp_path: Path) -> None:
    path = tmp_path / "motion"
    meta = record_scenario(motion_scenario(), path)
    assert rerun(path)["matches"] is True
    recorded = list(playback(path))
    gear = [r for r in recorded if r.get("command_kind") == "set_gear"]
    assert [r["accepted"] for r in gear] == [True, False]
    assert gear[1]["reason"] == "vehicle_moving"
    samples = [r for r in recorded if r["event_kind"] == "PhysicalMotionUpdated"]
    assert len(samples) == 120
    assert samples[99]["physical_speed_kph"] == pytest.approx(10.26)
    at_one_second = saved_view(path, 1_000_000_000)["state"]
    assert at_one_second["physical_speed_kph"] == pytest.approx(10.26)
    assert at_one_second["gear"] == "D"
    assert at_one_second["brake_percent"] == 0
    rejected = saved_view(path, 1_010_000_000)["state"]
    assert rejected["gear"] == "D"
    assert rejected["shift_rejection"] == "vehicle_moving"
    final = saved_view(path)["state"]
    assert isinstance(meta["final_state"], dict)
    assert final["physical_speed_kph"] == meta["final_state"]["physical_speed_kph"]
    assert final["brake_percent"] == 100
    assert isinstance(meta["configuration"], dict)
    motion = meta["configuration"].get("motion")
    assert isinstance(motion, dict)
    assert motion["brake_deceleration_mps2"] == 8


@pytest.mark.parametrize(
    "command",
    [
        {"kind": "set_brake", "percent": True},
        {"kind": "set_brake", "percent": 101},
        {"kind": "set_gear", "gear": "d"},
        {"kind": "set_gear", "gear": 1},
        {"kind": "set_gear", "gear": "D", "accepted": True},
    ],
)
def test_scenario_rejects_invalid_motion_controls(command: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Scenario.from_dict(
            {"schema_version": 1, "duration_ns": 0, "commands": [{"at_ns": 0, **command}]}
        )


@pytest.mark.parametrize(
    ("artifact", "kind", "changes", "message"),
    [
        (
            "commands.jsonl",
            "InputApplied",
            {"accepted": True, "reason": "brake_required"},
            "gear request result",
        ),
        (
            "commands.jsonl",
            "InputApplied",
            {"accepted": False, "reason": None},
            "gear request result",
        ),
        ("events.jsonl", "PhysicalMotionUpdated", {"physical_speed_kph": 181}, "physical speed"),
    ],
)
def test_import_rejects_malformed_motion_results_even_with_updated_checksum(
    tmp_path: Path, artifact: str, kind: str, changes: dict[str, object], message: str
) -> None:
    path = tmp_path / "run"
    record_scenario(motion_scenario(), path)
    records = [json.loads(line) for line in (path / artifact).read_text().splitlines()]
    target = next(
        r
        for r in records
        if r["event_kind"] == kind and (kind != "InputApplied" or r["command_kind"] == "set_gear")
    )
    target.update(changes)
    raw = ("\n".join(json.dumps(r) for r in records) + "\n").encode()
    (path / artifact).write_bytes(raw)
    meta = json.loads((path / "manifest.json").read_text())
    meta["artifacts"][artifact].update(bytes=len(raw), sha256=sha256(raw).hexdigest())
    (path / "manifest.json").write_text(json.dumps(meta))
    with pytest.raises(TraceError, match=message):
        verify_trace(path)
