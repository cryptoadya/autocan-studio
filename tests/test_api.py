"""M3 boundary: real core, trace artifacts, isolation and reconnect snapshots."""

import json
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient


def test_live_offline_recording_and_rerun(tmp_path: Path) -> None:
    from autocan.api.runtime import Session
    from autocan.traces import rerun

    session = Session(tmp_path, seed=42)
    try:
        session.inputs(50)
        session.start()
        session.tick()
        session.tick()
        assert session.snapshot()["state"]["kombi_rpm"] == 1000
        session.offline(True)
        session.tick()
        session.tick()
        assert session.snapshot()["state"]["kombi_status"] == "STALE"
        for _ in range(4):
            session.tick()
        state = session.snapshot()["state"]
        assert state["kombi_status"] == "TIMEOUT"
        assert state["kombi_rpm"] == 1000
        assert "physical_rpm" not in state
        physical = session.sim.state()["physical_rpm"]
        assert isinstance(physical, (int, float)) and physical > 1000
        session.pause()
        before = session.snapshot()["sim_time_ns"]
        session.tick()
        assert session.snapshot()["sim_time_ns"] == before
        session.stop()
        assert rerun(tmp_path / session.id)["matches"] is True
        with pytest.raises(ValueError, match="stopped"):
            session.start()
    finally:
        session.close()


def test_live_buffers_are_bounded_and_reset_preserves_saved_run(tmp_path: Path) -> None:
    from autocan.api.runtime import Session
    from autocan.traces import verify_trace

    session = Session(tmp_path, seed=0)
    try:
        session.start()
        for _ in range(440):
            session.tick()
        snapshot = session.snapshot()
        assert len(snapshot["frames"]) == 200
        assert len(snapshot["signals"]) == 400
        assert snapshot["state"]["frames_published"] == 1101
        session.stop()
        assert verify_trace(tmp_path / session.id)["status"] == "complete"
        fresh = Session(tmp_path, seed=0)
        try:
            assert fresh.id != session.id
            assert fresh.snapshot()["state"]["kombi_rpm"] is None
            assert fresh.snapshot()["frames"] == []
        finally:
            fresh.close()
    finally:
        session.close()


def test_rest_validation_ws_snapshot_reconnect_and_saved_playback(tmp_path: Path) -> None:
    from autocan.api import create_app

    with TestClient(create_app(data_root=tmp_path), base_url="http://127.0.0.1") as client:
        assert client.get("/api/health").status_code == 200
        created = client.post("/api/simulations", json={"seed": 42}).json()
        identifier = created["id"]
        base = f"/api/simulations/{identifier}"
        assert created["state"]["kombi_rpm"] is None
        assert client.post("/api/simulations", json={}).status_code == 409
        assert client.patch(base + "/inputs", json={"throttle": 101}).status_code == 422
        assert client.patch(base + "/inputs", json={"throttle": True}).status_code == 422
        assert client.post(base + "/faults", json={"offline": "yes"}).status_code == 422
        assert client.patch(base + "/inputs", json={"throttle": 50}).status_code == 200
        with client.websocket_connect(f"ws://127.0.0.1/ws/simulations/{identifier}") as ws:
            first = ws.receive_json()
            assert first["type"] == "snapshot"
            assert first["seq"] == 1
            assert first["schema_version"] == 1
        client.post(base + "/start")
        with client.websocket_connect(f"ws://127.0.0.1/ws/simulations/{identifier}") as ws:
            assert ws.receive_json()["type"] == "snapshot"
            delta = ws.receive_json()
            assert delta["type"] == "frames_batch"
            assert delta["seq"] == 2
            assert delta["state"]["kombi_rpm"] is not None
        assert client.post(base + "/stop").status_code == 200
        with client.websocket_connect(f"ws://127.0.0.1/ws/simulations/{identifier}") as ws:
            restored = ws.receive_json()
            assert restored["seq"] == 1
            assert restored["state"]["kombi_rpm"] is not None
            assert restored["frames"]
        runs = client.get("/api/runs").json()
        assert runs[0]["id"] == identifier
        saved = client.get(f"/api/runs/{identifier}/playback").json()
        assert saved["mode"] == "playback"
        assert saved["state"]["kombi_rpm"] == restored["state"]["kombi_rpm"]
        assert client.post(f"/api/runs/{identifier}/rerun").json()["matches"] is True
        reset = client.post(base + "/reset").json()
        assert reset["id"] != identifier
        assert client.get(f"/api/runs/{identifier}/playback").status_code == 200
        assert client.get(f"/api/simulations/{identifier}/state").status_code == 404
        assert client.get("/api/runs/missing/playback").status_code == 404


def test_cross_origin_and_host_are_rejected(tmp_path: Path) -> None:
    from autocan.api import create_app

    with TestClient(create_app(data_root=tmp_path), base_url="http://127.0.0.1") as client:
        assert (
            client.post(
                "/api/simulations", json={}, headers={"Origin": "https://untrusted.example"}
            ).status_code
            == 403
        )
        assert client.get("/api/health", headers={"Host": "untrusted.example"}).status_code == 400
        assert (
            client.post(
                "/api/simulations", json={}, headers={"Origin": "http://127.0.0.1"}
            ).status_code
            == 200
        )
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "ws://127.0.0.1/ws/simulations/missing",
                headers={"Origin": "https://untrusted.example"},
            ):
                pass


def test_saved_view_uses_delivery_and_deadlines_on_the_live_sample_grid(tmp_path: Path) -> None:
    from autocan.api.runtime import saved_view
    from autocan.scenarios import Scenario
    from autocan.traces import record_scenario

    record_scenario(
        Scenario.from_dict(
            {
                "schema_version": 1,
                "duration_ns": 30_000_000_000,
                "commands": [
                    {"at_ns": 0, "kind": "set_throttle", "percent": 50},
                    {"at_ns": 100_000_000, "kind": "set_engine_publishing", "enabled": False},
                ],
            }
        ),
        tmp_path / "saved",
    )
    timed_out = saved_view(tmp_path / "saved", 300_000_000)
    assert timed_out["state"]["kombi_status"] == "TIMEOUT"
    assert timed_out["state"]["kombi_rpm"] == 960
    end = saved_view(tmp_path / "saved")
    assert len(end["signals"]) == 400
    assert end["signals"][0]["sim_time_ns"] == 10_050_000_000
    assert end["signals"][-1]["sim_time_ns"] == 30_000_000_000


def test_playback_seek_verifies_once_and_rejects_corruption_after_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autocan import traces
    from autocan.api import create_app
    from autocan.scenarios import Scenario

    path = tmp_path / ("a" * 32)
    traces.record_scenario(
        Scenario.from_dict({"schema_version": 1, "duration_ns": 200_000_000, "commands": []}),
        path,
    )
    read_meta = Mock(wraps=traces._read_meta)
    monkeypatch.setattr(traces, "_read_meta", read_meta)
    seek = f"/api/runs/{path.name}/playback?until_ns=50000000"
    with TestClient(create_app(data_root=tmp_path), base_url="http://127.0.0.1") as client:
        response = client.get(seek)
        assert response.status_code == 200
        assert response.json()["sim_time_ns"] == 50_000_000
        calls_for_valid_seek = read_meta.call_count

        # Update the artifact's size and hash so the full event verification
        # has to find the corruption beyond the requested position.
        events = path / "events.jsonl"
        lines = events.read_text().splitlines()
        last = json.loads(lines[-1])
        assert last["sim_time_ns"] > 50_000_000
        last["event_seq"] += 1
        lines[-1] = json.dumps(last)
        raw = ("\n".join(lines) + "\n").encode()
        events.write_bytes(raw)
        manifest = path / "manifest.json"
        meta = json.loads(manifest.read_text())
        meta["artifacts"][events.name].update(bytes=len(raw), sha256=sha256(raw).hexdigest())
        manifest.write_text(json.dumps(meta))

        response = client.get(seek)
        assert response.status_code == 409
        assert response.json()["detail"] == "canonical event ordering mismatch"
        assert calls_for_valid_seek == 1


def test_api_body_and_run_command_limits_are_explicit(tmp_path: Path) -> None:
    from autocan.api import create_app
    from autocan.api.runtime import Session

    with TestClient(create_app(data_root=tmp_path), base_url="http://127.0.0.1") as client:
        assert client.post("/api/simulations", content=b" " * 4097).status_code == 413
        assert client.post("/api/simulations", json={"seed": True}).status_code == 422
    session = Session(tmp_path, seed=0)
    try:
        for _ in range(256):
            session.inputs(50)
        with pytest.raises(ValueError, match="command quota"):
            session.offline(True)
        session.start()
        session.tick()
        assert session.snapshot()["state"]["kombi_rpm"] == 880
    finally:
        session.close()


@pytest.mark.parametrize("profile_id", ["democar", "democar-network"])
def test_duration_limit_finalizes_a_complete_reproducible_recording(
    tmp_path: Path, profile_id: str
) -> None:
    from autocan.api.runtime import Session
    from autocan.traces import rerun

    session = Session(tmp_path, seed=42, profile_id=profile_id)
    try:
        session.start()
        for _ in range(2401):
            session.tick()
        snapshot = session.snapshot()
        assert snapshot["stopped"] is True
        assert snapshot["recording"] == "saved"
        assert snapshot["sim_time_ns"] == 120_000_000_000
        assert rerun(tmp_path / session.id)["matches"] is True
    finally:
        session.close()


def test_disk_failure_still_stops_session_when_failure_manifest_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autocan.api.runtime import Session
    from autocan.traces import TraceError, verify_trace

    session = Session(tmp_path, seed=42)
    try:
        session.start()
        session.tick()
        original_write = Path.write_bytes

        def disk_full(path: Path, data: bytes) -> int:
            if path.name == "manifest.tmp":
                raise OSError("disk full")
            return original_write(path, data)

        monkeypatch.setattr(Path, "write_bytes", disk_full)
        session.fail(OSError("disk full"))
        assert session.snapshot()["error"] == "disk full"
        assert session.snapshot()["recording"] == "failed"
        session.tick()
        with pytest.raises(TraceError):
            verify_trace(tmp_path / session.id)
    finally:
        session.close()
