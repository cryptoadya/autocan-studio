from pathlib import Path

from fastapi.testclient import TestClient

from autocan.api import create_app
from autocan.api.runtime import Session, saved_view
from autocan.traces import rerun


def test_network_live_and_saved_can_projections_match(tmp_path: Path) -> None:
    session = Session(tmp_path, seed=42, profile_id="democar-network")
    try:
        session.inputs(brake=100, gear="D")
        session.start()
        for _ in range(4):
            session.tick()
        session.inputs(throttle=70, brake=0)
        for _ in range(10):
            session.tick()
        session.offline(True, "abs")
        for _ in range(22):
            session.tick()
        live = session.snapshot()
        assert live["state"]["kombi_speed_status"] == "TIMEOUT"
        assert live["state"]["kombi_status"] == "FRESH"
        assert live["state"]["kombi_gear"] == "D"
        assert live["state"]["physical_speed_kph"] > live["state"]["kombi_speed_kph"]
        assert "physical_coolant_c" not in live["state"]
        session.stop()
        saved = saved_view(tmp_path / session.id)
        for key, value in saved["state"].items():
            if key not in {"paused", "sim_time_ns"}:
                assert live["state"][key] == value, key
        assert saved["frames"] == live["frames"]
        assert {f["message_name"] for f in saved["frames"]} == {
            "EngineStatus",
            "EngineTelemetry",
            "WheelSpeeds",
            "AbsStatus",
            "TransmissionState",
            "BodyState",
            "DriverInput",
            "ClusterHeartbeat",
        }
        assert all(f["receiver_id"] == "observer" for f in saved["frames"])
        assert rerun(tmp_path / session.id)["matches"] is True
    finally:
        session.close()


def test_network_api_profile_fault_selection_and_reset(tmp_path: Path) -> None:
    with TestClient(create_app(data_root=tmp_path), base_url="http://127.0.0.1") as client:
        profiles = client.get("/api/profiles").json()
        assert any(p["id"] == "democar-network" for p in profiles)
        created = client.post("/api/simulations", json={"profile_id": "democar-network"})
        assert created.status_code == 200
        base = f"/api/simulations/{created.json()['id']}"
        assert (
            client.post(base + "/faults", json={"offline": True, "ecu_id": "body"}).status_code
            == 200
        )
        assert (
            client.post(base + "/faults", json={"offline": True, "ecu_id": "unknown"}).status_code
            == 422
        )
        reset = client.post(base + "/reset").json()
        assert reset["state"]["profile_id"] == "democar-network"
        assert reset["state"]["body_publishing"] is True
        assert reset["state"]["kombi_speed_kph"] is None
