"""CLI demonstrations and interactive commands must replay without source files."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from autocan.traces import playback
from autocan.validation import number


def test_shell_inject_clear_and_record_applied_commands(tmp_path: Path) -> None:
    spec = tmp_path / "spike.json"
    spec.write_text(
        json.dumps(
            {
                "fault_id": "manual-spike",
                "type": "signal_spike",
                "target_ecu": "engine",
                "target_message_id": 512,
                "signal": "EngineRpm",
                "parameters": {"value": 7000},
            }
        )
    )
    target = tmp_path / "recording"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "autocan",
            "shell",
            "--profile",
            "democar-network",
            "--record",
            str(target),
        ],
        input=f'inject "{spec}"\nrun 0\nreset\nclear manual-spike\nrun 20\nquit\n',
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "reset" in result.stderr
    states = [json.loads(line) for line in result.stdout.splitlines()]
    assert states[1]["kombi_rpm"] == 7000
    assert states[-1]["kombi_rpm"] == 800
    spec.unlink()
    rerun = subprocess.run(
        [sys.executable, "-m", "autocan", "rerun", str(target)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert rerun.returncode == 0, rerun.stderr
    assert json.loads(rerun.stdout)["matches"] is True


def test_shell_runtime_dbc_error_marks_recording_failed(tmp_path: Path) -> None:
    spec = tmp_path / "bias.json"
    spec.write_text(
        json.dumps(
            {
                "fault_id": "invalid-bias",
                "type": "signal_bias",
                "target_ecu": "engine",
                "target_message_id": 512,
                "signal": "EngineRpm",
                "parameters": {"offset": 8000},
            }
        )
    )
    target = tmp_path / "failed"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "autocan",
            "shell",
            "--profile",
            "democar-network",
            "--record",
            str(target),
        ],
        input=f'inject "{spec}"\nrun 0\nquit\n',
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "outside DBC range" in result.stderr
    assert json.loads((target / "manifest.json").read_text())["status"] == "failed"


@pytest.mark.parametrize("name", ["abs_missing_frames", "rpm_spike", "timing_jitter"])
def test_cli_fault_demos_match_golden_observations_and_rerun(name: str, tmp_path: Path) -> None:
    target = tmp_path / name
    recorded = subprocess.run(
        [sys.executable, "-m", "autocan", "run", f"scenarios/{name}.json", "--output", str(target)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert recorded.returncode == 0, recorded.stderr
    events = list(playback(target))
    golden = json.loads(Path("tests/fixtures/m6_faults_golden.json").read_text())[name]
    if name == "abs_missing_frames":
        actual = [
            [e["sim_time_ns"], e["status"]]
            for e in events
            if e["event_kind"] == "EcuStatusChanged"
            and e["ecu_id"] == "KOMBI"
            and e["message_name"] == "WheelSpeeds"
        ]
        assert actual == golden["wheel_status"]
        speed = [
            number(e["physical_speed_kph"], "speed", minimum=-30, maximum=180)
            for e in events
            if e["event_kind"] == "PhysicalMotionUpdated"
        ]
        assert len(speed) > 20 and max(speed) > 0
    else:
        actual = [
            [e["sim_time_ns"], e["payload_hex"]]
            for e in events
            if e["event_kind"] == "FramePublished" and e["can_id"] == 512
        ]
        assert actual == golden["engine_frames"]
        if name == "timing_jitter":
            frames = {e["frame_id"]: e for e in events if e["event_kind"] == "FramePublished"}
            assert [
                [e["sim_time_ns"], frames[e["frame_id"]]["created_at_ns"]]
                for e in events
                if e["event_kind"] == "FrameReceived"
                and e["receiver_id"] == "observer"
                and frames[e["frame_id"]]["can_id"] == 512
            ] == golden["observer_engine_deliveries"]
    repeated = subprocess.run(
        [sys.executable, "-m", "autocan", "rerun", str(target)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(repeated.stdout)["matches"] is True
