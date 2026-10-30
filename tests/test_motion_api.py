"""Brake/gear batches must validate atomically and expose model speed explicitly."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autocan.api import create_app
from autocan.api.runtime import Session


def test_input_batch_applies_brake_before_gear_and_reports_rejection(tmp_path: Path) -> None:
    session = Session(tmp_path, seed=42)
    try:
        assert session.inputs(100, brake=100, gear="D") == 0
        session.start()
        session.tick()
        assert session.snapshot()["state"]["gear"] == "D"
        assert session.snapshot()["state"]["physical_speed_kph"] == 0
        session.inputs(brake=0)
        for _ in range(20):
            session.tick()
        state = session.snapshot()["state"]
        assert state["physical_speed_kph"] == pytest.approx(10.26)
        assert "physical_rpm" not in state
        session.inputs(gear="R")
        session.tick()
        state = session.snapshot()["state"]
        assert state["gear"] == "D"
        assert state["shift_rejection"] == "vehicle_moving"
        assert session.stopped is False
    finally:
        session.close()


@pytest.mark.parametrize(
    "body",
    [
        {"brake": -1},
        {"brake": True},
        {"brake": None},
        {"brake": "50"},
        {"gear": "d"},
        {"gear": None},
        {"gear": True},
        {"physical_speed_kph": 10},
        {"brake": 100, "gear": "X"},
    ],
)
def test_api_rejects_invalid_motion_inputs_without_queueing(tmp_path: Path, body: object) -> None:
    with TestClient(create_app(data_root=tmp_path), base_url="http://127.0.0.1") as client:
        identifier = client.post("/api/simulations", json={}).json()["id"]
        assert client.patch(f"/api/simulations/{identifier}/inputs", json=body).status_code == 422
        state = client.get(f"/api/simulations/{identifier}/state").json()["state"]
        assert state["gear"] == "P"
        assert state["brake_percent"] == 0
        assert state["events_emitted"] == 0


def test_motion_api_accepts_queued_brake_and_gear_commands(tmp_path: Path) -> None:
    with TestClient(create_app(data_root=tmp_path), base_url="http://127.0.0.1") as client:
        identifier = client.post("/api/simulations", json={}).json()["id"]
        response = client.patch(
            f"/api/simulations/{identifier}/inputs", json={"brake": 100, "gear": "D"}
        )
        assert response.status_code == 200
        assert response.json()["applied_at_ns"] == 0
        assert client.get(f"/api/simulations/{identifier}/state").json()["state"]["gear"] == "P"


def test_motion_batch_reserves_every_command_before_queueing(tmp_path: Path) -> None:
    session = Session(tmp_path, seed=42)
    try:
        for _ in range(254):
            session.inputs(0)
        with pytest.raises(ValueError, match="quota"):
            session.inputs(50, brake=100, gear="D")
        assert session.commands == 254
        session.start()
        session.tick()
        assert session.snapshot()["state"]["gear"] == "P"
        assert session.snapshot()["state"]["brake_percent"] == 0
    finally:
        session.close()
