"""Shell controls and saved scenarios must reach the same physical command path."""

import json
import subprocess
import sys
from pathlib import Path


def test_shell_stops_restarts_and_changes_ignition_without_disabling_can() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "autocan", "shell"],
        input="engine stop\nrun 400\nengine start\nrun 400\nignition ACC\nrun 400\nquit\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert not result.stderr
    states = [json.loads(line) for line in result.stdout.splitlines()]
    assert states[1]["engine_running"] is False
    assert states[1]["kombi_rpm"] == 0
    assert states[1]["kombi_status"] == "FRESH"
    assert states[3]["engine_running"] is True
    assert states[3]["kombi_rpm"] == 800
    assert states[5]["engine_running"] is False
    assert states[5]["ignition_state"] == "ACC"
    assert states[5]["engine_publishing"] is True


def test_example_engine_controls_scenario_is_recordable_and_reproducible(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "autocan",
            "run",
            "scenarios/engine_start_stop.json",
            "--output",
            str(tmp_path / "run"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    final = json.loads(result.stdout)["final_state"]
    assert final["kombi_rpm"] == 580
    assert final["engine_running"] is False
    assert final["kombi_status"] == "FRESH"
    rerun = subprocess.run(
        [sys.executable, "-m", "autocan", "rerun", str(tmp_path / "run")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert rerun.returncode == 0, rerun.stderr
    assert json.loads(rerun.stdout)["matches"] is True
