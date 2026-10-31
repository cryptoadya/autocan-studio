"""M6 scenarios and manual faults use the canonical recorder through REST/WS."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autocan.api import create_app
from autocan.api.runtime import Session, saved_view
from autocan.faults import FaultSpec
from autocan.traces import rerun


def spike(identifier: str = "manual-spike") -> FaultSpec:
    return FaultSpec(identifier, "signal_spike", "engine", 512, {"value": 7000}, "EngineRpm")


def test_manual_fault_pause_timeline_clear_and_saved_projection(tmp_path: Path) -> None:
    session = Session(tmp_path, seed=42, profile_id="democar-network")
    try:
        assert session.inject_fault(spike()) == 0
        assert session.snapshot()["active_faults"] == []
        session.start()
        session.tick()
        live = session.snapshot()
        assert live["state"]["kombi_rpm"] == 7000
        assert live["active_faults"][0]["fault"]["fault_id"] == "manual-spike"
        assert any(e["event_kind"] == "FaultActivated" for e in live["timeline"])
        session.pause()
        session.clear_fault("manual-spike")
        assert len(session.snapshot()["active_faults"]) == 1
        session.start()
        session.tick()
        assert session.snapshot()["state"]["kombi_rpm"] == 800
        assert session.snapshot()["active_faults"] == []
        session.stop()
        saved = saved_view(tmp_path / session.id, 50_000_000)
        assert saved["state"]["kombi_rpm"] == 7000
        assert len(saved["active_faults"]) == 1
        assert not any(e["event_kind"] == "FaultCleared" for e in saved["timeline"])
        end = saved_view(tmp_path / session.id)
        assert end["timeline"] == session.snapshot()["timeline"]
        assert end["active_faults"] == []
        assert rerun(tmp_path / session.id)["matches"] is True
    finally:
        session.close()


@pytest.mark.parametrize("identifier", ["abs_missing_frames", "rpm_spike", "timing_jitter"])
def test_catalog_scenario_stops_exactly_and_reruns(identifier: str, tmp_path: Path) -> None:
    from autocan.catalog import load_demo

    descriptor, scenario = load_demo(identifier)
    session = Session(
        tmp_path,
        seed=scenario.seed,
        profile_id=scenario.profile_id,
        scenario=scenario,
        scenario_info=descriptor,
    )
    try:
        session.start()
        for _ in range(20):
            session.tick()
        view = session.snapshot()
        assert view["stopped"] is True
        assert view["sim_time_ns"] == scenario.duration_ns
        assert view["active_faults"] == []
        assert any(e["event_kind"] == "FaultActivated" for e in view["timeline"])
        saved = saved_view(tmp_path / session.id)
        assert saved["scenario"]["id"] == identifier
        assert saved["timeline"] == view["timeline"]
        if identifier == "abs_missing_frames":
            mid = saved_view(tmp_path / session.id, 400_000_000)
            assert mid["state"]["kombi_speed_status"] == "TIMEOUT"
            assert mid["state"]["physical_speed_kph"] > mid["state"]["kombi_speed_kph"]
        elif identifier == "rpm_spike":
            assert saved_view(tmp_path / session.id, 150_000_000)["state"]["kombi_rpm"] == 7000
        else:
            assert any(
                f["sim_time_ns"] != f["created_at_ns"] for f in view["frames"] if f["can_id"] == 512
            )
        assert rerun(tmp_path / session.id)["matches"] is True
    finally:
        session.close()


def test_rest_catalog_validation_quota_stopped_run_and_reset(tmp_path: Path) -> None:
    with TestClient(create_app(data_root=tmp_path), base_url="http://127.0.0.1") as client:
        demos = client.get("/api/scenarios")
        assert demos.status_code == 200
        assert {s["id"] for s in demos.json()} == {
            "abs_missing_frames",
            "rpm_spike",
            "timing_jitter",
        }
        catalog = client.get("/api/faults").json()
        assert len(catalog["types"]) == 8
        assert any(
            m["can_id"] == 512 and m["signals"][0]["name"] == "EngineRpm"
            for m in catalog["messages"]
        )
        assert client.post("/api/simulations", json={"scenario_id": "../../bad"}).status_code == 422
        assert (
            client.post(
                "/api/simulations", json={"scenario_id": "rpm_spike", "profile_id": "democar"}
            ).status_code
            == 422
        )
        created = client.post("/api/simulations", json={"scenario_id": "rpm_spike"})
        assert created.status_code == 200
        body = created.json()
        assert body["state"]["profile_id"] == "democar-network"
        assert body["scenario"]["id"] == "rpm_spike"
        base = f"/api/simulations/{body['id']}"
        invalid = spike().as_dict() | {"parameters": {"value": 9000}}
        assert client.post(base + "/faults", json={"fault": invalid}).status_code == 422
        assert (
            client.post(
                base + "/faults", json={"fault": spike().as_dict(), "offline": True}
            ).status_code
            == 422
        )
        assert client.post(base + "/faults", json={"fault": spike().as_dict()}).status_code == 200
        assert client.post(base + "/faults/manual-spike/clear").status_code == 200
        reset = client.post(base + "/reset").json()
        assert reset["scenario"]["id"] == "rpm_spike"
        assert reset["active_faults"] == reset["timeline"] == []
        new_base = f"/api/simulations/{reset['id']}"
        assert client.post(new_base + "/stop").status_code == 200
        assert (
            client.post(new_base + "/faults", json={"fault": spike().as_dict()}).status_code == 409
        )
        assert client.post(new_base + "/start").status_code == 409
        preserved = client.get(new_base + "/state").json()
        assert preserved["recording"] == "saved"
        assert preserved["error"] is None


def test_invalid_faults_do_not_consume_command_quota(tmp_path: Path) -> None:
    session = Session(tmp_path, seed=42)
    try:
        with pytest.raises(ValueError, match="network"):
            session.inject_fault(spike())
        assert session.commands == 0
    finally:
        session.close()


def test_ws_delivers_late_lower_frame_ids_in_arrival_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autocan.api as api

    session = Session(tmp_path, seed=42, profile_id="democar-network")

    def existing_session(root: Path, **options: object) -> Session:
        return session

    monkeypatch.setattr(api, "Session", existing_session)
    # Drive the real virtual clock from this test, independent of wall-clock pacing.
    monkeypatch.setattr(Session, "tick", lambda self: None)
    with TestClient(create_app(data_root=tmp_path), base_url="http://127.0.0.1") as client:
        created = client.post("/api/simulations", json={"profile_id": "democar-network"}).json()
        session.inject_fault(
            FaultSpec(
                "jitter",
                "message_delay",
                "engine",
                512,
                {"min_ns": 100_000_000, "max_ns": 100_000_000},
            )
        )
        session.sim.clear_fault("jitter", at_ns=20_000_000)
        with client.websocket_connect(f"ws://127.0.0.1/ws/simulations/{created['id']}") as ws:
            assert ws.receive_json()["frames"] == []
            for _ in range(8):
                session.sim.step()
            first = ws.receive_json()
            assert any(f["can_id"] == 512 for f in first["frames"])
            for _ in range(2):
                session.sim.step()
            second = ws.receive_json()
            engine = [f for f in second["frames"] if f["can_id"] == 512]
            assert any(f["frame_id"] == 1 and f["created_at_ns"] == 0 for f in engine)
            assert second["timeline"]
        with client.websocket_connect(f"ws://127.0.0.1/ws/simulations/{created['id']}") as ws:
            restored = ws.receive_json()
            assert restored["timeline"] == session.snapshot()["timeline"]
            assert restored["active_faults"] == []


def test_fault_quota_and_timeline_bound(tmp_path: Path) -> None:
    session = Session(tmp_path, seed=42, profile_id="democar-network")
    try:
        with pytest.raises(ValueError, match="fault_id"):
            session.clear_fault("invalid/id")
        assert session.commands == 0
        for _ in range(256):
            session.clear_fault("unknown")
        with pytest.raises(ValueError, match="quota"):
            session.inject_fault(spike())
        session.start()
        session.tick()
        snapshot = session.snapshot()
        assert len(snapshot["timeline"]) == 100
        assert snapshot["timeline_truncated"] > 0
        assert snapshot["active_faults"] == []
        session.stop()
        assert rerun(tmp_path / session.id)["matches"] is True
    finally:
        session.close()
