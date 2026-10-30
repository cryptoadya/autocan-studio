"""CLI scenario recording, saved playback and independent rerun."""

import json
import subprocess
import sys
from pathlib import Path


def test_cli_records_plays_and_reruns_without_scenario_file(tmp_path: Path) -> None:
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "duration_ns": 400_000_000,
                "seed": 42,
                "commands": [
                    {"at_ns": 0, "kind": "set_throttle", "percent": 50},
                    {"at_ns": 100_000_000, "kind": "set_engine_publishing", "enabled": False},
                ],
            }
        )
    )
    target = tmp_path / "recording"
    recorded = subprocess.run(
        [sys.executable, "-m", "autocan", "run", str(scenario), "--output", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert recorded.returncode == 0, recorded.stderr
    assert json.loads(recorded.stdout)["status"] == "complete"
    scenario.unlink()
    played = subprocess.run(
        [sys.executable, "-m", "autocan", "playback", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert played.returncode == 0, played.stderr
    events = [json.loads(line) for line in played.stdout.splitlines()]
    assert events[-1]["status"] == "TIMEOUT"
    repeated = subprocess.run(
        [sys.executable, "-m", "autocan", "rerun", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(repeated.stdout)["matches"] is True


def test_cli_corrupt_run_fails_cleanly_without_partial_playback(tmp_path: Path) -> None:
    target = tmp_path / "broken"
    target.mkdir()
    (target / "manifest.json").write_text('{"status":"recording"}')
    result = subprocess.run(
        [sys.executable, "-m", "autocan", "playback", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "complete" in result.stderr
    assert "Traceback" not in result.stderr


def test_shell_offline_and_online_control_publication_only() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "autocan", "shell"],
        input="throttle 50\nrun 0\noffline\nrun 300\nonline\nrun 20\nquit\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    states = [json.loads(line) for line in result.stdout.splitlines()]
    assert states[3]["kombi_status"] == "TIMEOUT"
    assert states[3]["physical_rpm"] == 1400
    assert states[3]["engine_running"] is True
    assert states[-1]["kombi_status"] == "FRESH"
