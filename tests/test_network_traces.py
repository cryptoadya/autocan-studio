from pathlib import Path

from autocan.scenarios import Scenario
from autocan.traces import playback, record_scenario, rerun
from autocan.validation import number, object_value


def network_scenario() -> Scenario:
    return Scenario.from_dict(
        {
            "schema_version": 1,
            "profile_id": "democar-network",
            "duration_ns": 1_000_000_000,
            "seed": 42,
            "commands": [
                {"at_ns": 0, "kind": "set_brake", "percent": 100},
                {"at_ns": 0, "kind": "set_gear", "gear": "D"},
                {"at_ns": 100_000_000, "kind": "set_throttle", "percent": 50},
                {"at_ns": 100_000_000, "kind": "set_brake", "percent": 0},
                {
                    "at_ns": 150_000_000,
                    "kind": "set_ecu_publishing",
                    "ecu_id": "abs",
                    "enabled": False,
                },
                {
                    "at_ns": 350_000_000,
                    "kind": "set_ecu_publishing",
                    "ecu_id": "abs",
                    "enabled": True,
                },
            ],
        }
    )


def test_network_recording_verifies_per_receiver_references_and_reruns(tmp_path: Path) -> None:
    path = tmp_path / "network"
    manifest = record_scenario(network_scenario(), path)
    assert manifest["profile_version"] == "democar-0.2.0"
    records = list(playback(path))
    deliveries = [
        (r["frame_id"], r["receiver_id"]) for r in records if r["event_kind"] == "FrameReceived"
    ]
    assert len(deliveries) == len(set(deliveries))
    assert len({r for _, r in deliveries}) == 6
    assert sum(frame_id == 1 for frame_id, _ in deliveries) == 3
    assert any(r["event_kind"] == "GearShiftEvaluated" and r["accepted"] for r in records)
    assert rerun(path)["matches"] is True


def test_network_delayed_and_dropped_deliveries_reproduce(tmp_path: Path) -> None:
    base = network_scenario()
    scenario = Scenario(
        base.duration_ns,
        base.commands,
        seed=42,
        delivery_delay_ns=30_000_000,
        drop_probability=0.2,
        profile_id="democar-network",
    )
    path = tmp_path / "transport"
    record_scenario(scenario, path)
    records = list(playback(path))
    assert any(r["event_kind"] == "FrameDropped" for r in records)
    assert rerun(path)["matches"] is True


def test_normal_urban_network_drive_acceptance(tmp_path: Path) -> None:
    from autocan.api.runtime import saved_view

    path = tmp_path / "urban"
    manifest = record_scenario(Scenario.load(Path("scenarios/normal_urban.json")), path)
    events = list(playback(path))
    shifts = [e for e in events if e["event_kind"] == "GearShiftEvaluated"]
    assert all(e["accepted"] for e in shifts)
    assert [e["requested_gear"] for e in shifts] == ["P", "D", "N", "D", "P"]
    speeds = [
        number(e["physical_speed_kph"], "speed", minimum=-30, maximum=180)
        for e in events
        if e["event_kind"] == "PhysicalMotionUpdated"
    ]
    assert max(speeds) > 20
    assert speeds[-1] == 0
    final = object_value(manifest["final_state"], "final state")
    assert final["gear"] == "P"
    assert final["engine_running"] is False
    view = saved_view(path)
    for key in (
        "kombi_status",
        "kombi_speed_status",
        "kombi_temperature_status",
        "kombi_gear_status",
        "kombi_body_status",
        "kombi_abs_status",
        "kombi_heartbeat_status",
    ):
        assert view["state"][key] == "FRESH", key
    assert view["state"]["kombi_speed_kph"] == 0
    assert view["state"]["kombi_gear"] == "P"
    assert view["state"]["kombi_engine_on"] is False
    assert view["state"]["kombi_ignition"] == "OFF"
    assert rerun(path)["matches"] is True
