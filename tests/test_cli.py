"""Exercise the shipped command interface with real virtual CAN transport."""

import json
import subprocess
import sys

import pytest


def test_demo_prints_observed_rpm_and_frame_counts() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "autocan", "demo", "--duration", "0.1", "--throttle", "50"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["sim_time_ns"] == 100_000_000
    assert state["kombi_rpm"] == 1000
    assert state["frames_published"] == state["deliveries"] == 6


def test_demo_streams_canonical_events_separately_from_summary() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "autocan", "demo", "--duration", "0", "--events"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [event["event_kind"] for event in events] == [
        "InputApplied",
        "FramePublished",
        "FrameReceived",
        "EcuStatusChanged",
    ]
    assert events[1]["payload_hex"] == "800c"
    assert events[2]["receiver_id"] == "KOMBI"
    assert json.loads(result.stderr)["kombi_rpm"] == 800


def test_shell_pause_step_state_and_reset() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "autocan", "shell"],
        input="run 0\npause\nthrottle 50\nrun 100\nstep\nresume\nrun 10\nreset\nstate\nquit\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    states = [json.loads(line) for line in result.stdout.splitlines()]
    assert states[3]["sim_time_ns"] == 0
    assert states[4]["sim_time_ns"] == 10_000_000
    assert states[4]["paused"] is True
    assert states[6]["kombi_rpm"] == 840
    assert states[-1]["kombi_rpm"] is None
    assert states[-1]["frames_published"] == 0


@pytest.mark.parametrize(
    "duration", ["-1", "nan", "inf", "0.0000000001", "0.10000000000000000000000000000000000001"]
)
def test_demo_rejects_invalid_duration(duration: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "autocan", "demo", "--duration", duration],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert "duration" in result.stderr.lower()


def test_shell_rejects_bad_command_and_continues_without_changing_inputs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "autocan", "shell"],
        input="throttle nan\nstate\nquit\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "error" in result.stderr.lower()
    assert json.loads(result.stdout)["throttle_percent"] == 0
