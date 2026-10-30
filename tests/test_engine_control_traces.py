"""Applied physical controls must survive strict scenario import and real rerun."""

from pathlib import Path

import pytest

from autocan.api.runtime import saved_view
from autocan.scenarios import Scenario
from autocan.traces import playback, record_scenario, rerun


def engine_controls_scenario() -> dict[str, object]:
    return {
        "schema_version": 1,
        "duration_ns": 1_000_000_000,
        "commands": [
            {"at_ns": 0, "kind": "set_ignition", "state": "OFF"},
            {"at_ns": 400_000_000, "kind": "set_ignition", "state": "ON"},
            {"at_ns": 500_000_000, "kind": "set_engine_running", "enabled": True},
            {"at_ns": 900_000_000, "kind": "set_engine_running", "enabled": False},
        ],
    }


def test_ignition_and_engine_commands_record_play_back_and_rerun(tmp_path: Path) -> None:
    meta = record_scenario(Scenario.from_dict(engine_controls_scenario()), tmp_path / "controls")
    commands = [e for e in playback(tmp_path / "controls") if e["event_kind"] == "InputApplied"]
    assert [e["command_kind"] for e in commands] == [
        "set_ignition",
        "set_ignition",
        "set_engine_running",
        "set_engine_running",
    ]
    assert rerun(tmp_path / "controls")["matches"] is True
    assert isinstance(meta["final_state"], dict)
    assert meta["final_state"]["engine_running"] is False
    assert meta["final_state"]["physical_rpm"] == 580
    assert meta["final_state"]["kombi_status"] == "FRESH"
    # This is a physical control sequence, not injected communication loss.
    assert not (tmp_path / "controls/faults.jsonl").read_text()


def test_playback_control_state_is_reconstructed_without_becoming_a_can_measurement(
    tmp_path: Path,
) -> None:
    path = tmp_path / "controls"
    record_scenario(Scenario.from_dict(engine_controls_scenario()), path)
    off = saved_view(path, 400_000_000)["state"]
    assert off["engine_running"] is False
    assert off["ignition_state"] == "ON"
    assert off["kombi_rpm"] == 0
    assert off["engine_publishing"] is True
    running = saved_view(path, 600_000_000)["state"]
    assert running["engine_running"] is True
    assert running["ignition_state"] == "ON"
    assert running["kombi_rpm"] == 220


@pytest.mark.parametrize(
    ("ignition_request", "expected_ignition", "expected_running"),
    [("OFF", "OFF", False), ("ACC", "ACC", False), ("ON", "ON", False), ("START", "ON", True)],
)
def test_playback_reconstructs_momentary_start_and_nonstarting_ignition(
    tmp_path: Path, ignition_request: str, expected_ignition: str, expected_running: bool
) -> None:
    scenario = Scenario.from_dict(
        {
            "schema_version": 1,
            "duration_ns": 200_000_000,
            "commands": [
                {"at_ns": 0, "kind": "set_engine_running", "enabled": False},
                {"at_ns": 100_000_000, "kind": "set_ignition", "state": ignition_request},
            ],
        }
    )
    record_scenario(scenario, tmp_path / "run")
    state = saved_view(tmp_path / "run")["state"]
    assert state["ignition_state"] == expected_ignition
    assert state["engine_running"] is expected_running
    assert state["engine_publishing"] is True


@pytest.mark.parametrize(
    "command",
    [
        {"kind": "set_ignition", "state": "on"},
        {"kind": "set_ignition", "state": True},
        {"kind": "set_ignition", "state": "OFF", "enabled": False},
        {"kind": "set_engine_running", "enabled": 1},
        {"kind": "set_engine_running", "enabled": "true"},
        {"kind": "set_engine_running", "enabled": False, "state": "OFF"},
    ],
)
def test_invalid_engine_controls_are_rejected_at_scenario_import(
    command: dict[str, object],
) -> None:
    data = engine_controls_scenario()
    data["commands"] = [{"at_ns": 0, **command}]
    with pytest.raises(ValueError):
        Scenario.from_dict(data)


def test_v01_recording_remains_playable_with_the_extended_importer() -> None:
    from autocan.traces import TraceError

    legacy = Path(__file__).parent / "fixtures/v0_1_engine_offline"
    assert len(list(playback(legacy))) == 41
    with pytest.raises(TraceError, match="incompatible"):
        rerun(legacy)
