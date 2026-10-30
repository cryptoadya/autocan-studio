import json
import subprocess
import sys
from pathlib import Path


def test_shell_drives_brakes_and_reverses_with_a_nonfatal_rejected_shift() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "autocan", "shell"],
        input=(
            "brake 100\ngear D\nthrottle 100\nbrake 0\nrun 1000\ngear R\nrun 100\n"
            "gear N\nbrake 100\nrun 1000\ngear R\nbrake 0\nrun 1000\nquit\n"
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    states = [json.loads(line) for line in result.stdout.splitlines()]
    assert states[4]["physical_speed_kph"] == 10.26
    assert states[6]["gear"] == "D"
    assert states[6]["shift_rejection"] == "vehicle_moving"
    assert states[9]["physical_speed_kph"] == 0
    assert states[-1]["gear"] == "R"
    assert states[-1]["physical_speed_kph"] < 0
    assert states[-1]["kombi_status"] == "FRESH"


def test_motion_example_records_and_reruns_from_the_public_cli(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "autocan",
            "run",
            "scenarios/motion_controls.json",
            "--output",
            str(tmp_path / "run"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    final = json.loads(result.stdout)["final_state"]
    assert final["gear"] == "R"
    assert final["physical_speed_kph"] < 0
    check = subprocess.run(
        [sys.executable, "-m", "autocan", "rerun", str(tmp_path / "run")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stderr
    assert json.loads(check.stdout)["matches"] is True
