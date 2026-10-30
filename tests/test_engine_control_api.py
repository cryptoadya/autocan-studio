"""Physical controls share queued API commands, quotas and CAN-only measurements."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autocan.api import create_app
from autocan.api.runtime import Session


def test_api_stop_keeps_fresh_can_and_saved_physical_control_state(tmp_path: Path) -> None:
    with TestClient(create_app(data_root=tmp_path), base_url="http://127.0.0.1") as client:
        initial = client.post("/api/simulations", json={"seed": 42}).json()
        identifier = initial["id"]
        response = client.patch(f"/api/simulations/{identifier}/inputs", json={"ignition": "OFF"})
        assert response.status_code == 200
        assert response.json()["applied_at_ns"] == 0
        assert client.get(f"/api/simulations/{identifier}/state").json()["state"]["engine_running"]
        client.post(f"/api/simulations/{identifier}/start")
        with client.websocket_connect(f"ws://127.0.0.1/ws/simulations/{identifier}") as ws:
            for _ in range(30):
                state = ws.receive_json()["state"]
                if state["sim_time_ns"] >= 400_000_000:
                    break
            assert state["sim_time_ns"] >= 400_000_000
            assert state["engine_running"] is False
            assert state["ignition_state"] == "OFF"
            assert state["engine_publishing"] is True
            assert state["kombi_rpm"] == 0
            assert state["kombi_status"] == "FRESH"
            assert "physical_rpm" not in state
        assert client.post(f"/api/simulations/{identifier}/stop").status_code == 200
        saved = client.get(f"/api/runs/{identifier}/playback").json()
        assert saved["state"]["engine_running"] is False
        assert saved["state"]["ignition_state"] == "OFF"
        assert client.post(f"/api/runs/{identifier}/rerun").json()["matches"] is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"engine_running": None},
        {"engine_running": 1},
        {"engine_running": "true"},
        {"ignition": None},
        {"ignition": "on"},
        {"ignition": True},
        {"throttle": None},
        {"engine_running": True, "ignition": None},
        {"brake": 10},
    ],
)
def test_api_rejects_invalid_controls_before_queueing(tmp_path: Path, body: object) -> None:
    with TestClient(create_app(data_root=tmp_path), base_url="http://127.0.0.1") as client:
        identifier = client.post("/api/simulations", json={}).json()["id"]
        assert client.patch(f"/api/simulations/{identifier}/inputs", json=body).status_code == 422
        state = client.get(f"/api/simulations/{identifier}/state").json()["state"]
        assert state["engine_running"] is True
        assert state["ignition_state"] == "ON"
        assert state["events_emitted"] == 0


def test_input_batch_reserves_command_quota_before_queuing_any_part(tmp_path: Path) -> None:
    session = Session(tmp_path, seed=42)
    try:
        for _ in range(255):
            session.inputs(0)
        with pytest.raises(ValueError, match="quota"):
            session.inputs(50, ignition="OFF", engine_running=False)
        assert session.commands == 255
        session.start()
        session.tick()
        assert session.snapshot()["state"]["throttle_percent"] == 0
        assert session.snapshot()["state"]["engine_running"] is True
    finally:
        session.close()


def test_batch_order_is_throttle_then_ignition_then_engine_control(tmp_path: Path) -> None:
    session = Session(tmp_path, seed=42)
    try:
        assert session.inputs(50, ignition="OFF", engine_running=True) == 0
        session.start()
        session.tick()
        state = session.snapshot()["state"]
        assert state["engine_running"] is True
        assert state["ignition_state"] == "ON"
        assert state["throttle_percent"] == 50
        assert session.commands == 3
    finally:
        session.close()
