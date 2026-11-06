"""Saved score points and causal alert episodes survive playback and trace exchange."""

from pathlib import Path

from autocan.api.runtime import Session, saved_view
from autocan.faults import FaultSpec
from autocan.parquet import export_trace, import_trace
from autocan.simulation import Simulation
from autocan.traces import TraceRecorder, rerun

MS = 1_000_000


def test_recorded_alert_episodes_ignore_warmup_and_bridge_one_low_score(tmp_path: Path) -> None:
    run = tmp_path / "run"
    with (
        TraceRecorder(run) as recorder,
        Simulation(profile_id="democar-network", on_event=recorder.write) as simulation,
    ):
        recorder.begin(
            simulation,
            live_model={
                "model_id": "random_forest",
                "model_sha256": "a" * 64,
                "threshold": 0.5,
                "feature_schema_version": 1,
            },
        )
        for index, score in enumerate([0.9, 0.5, 0.2, 0.8, 0.1, 0.2], start=1):
            end = index * 100 * MS
            simulation.advance_to(end)
            recorder.write_score(
                {
                    "sim_time_ns": end,
                    "score": score,
                    "eligible": index > 1,
                    "timed_out_can_ids": [],
                    "rpm_derivative_max": None,
                    "wheel_residual_max": None,
                }
            )
        recorder.finish(simulation.state())

    warmup = saved_view(run, 100 * MS)["anomaly"]
    assert len(warmup["scores"]) == 1 and warmup["alerts"] == []
    earlier = saved_view(run, 500 * MS)["anomaly"]
    assert len(earlier["scores"]) == 5
    assert earlier["alerts"] == [{"start_ns": 200 * MS, "end_ns": None, "peak_score": 0.8}]
    final = saved_view(run)["anomaly"]
    assert final["alerts"] == [{"start_ns": 200 * MS, "end_ns": 600 * MS, "peak_score": 0.8}]

    bundle, restored = tmp_path / "bundle", tmp_path / "restored"
    export_trace(run, bundle)
    import_trace(bundle, restored)
    assert saved_view(restored)["anomaly"] == final
    assert rerun(restored)["matches"] is True


def test_active_fault_markers_survive_journal_truncation_in_live_and_playback(
    tmp_path: Path,
) -> None:
    session = Session(tmp_path, seed=42, profile_id="democar-network")
    try:
        session.offline(True, "abs")
        session.inject_fault(
            FaultSpec.from_dict(
                {
                    "fault_id": "engine-library",
                    "type": "ecu_offline",
                    "target_ecu": "engine",
                    "target_message_id": None,
                    "parameters": {},
                    "seed": 42,
                }
            )
        )
        session.start()
        for throttle in range(110):
            session.inputs(throttle=throttle % 100)
            session.tick()
        live = session.snapshot()
        assert live["timeline_truncated"] > 0
        assert not any(row["event_kind"] == "FaultActivated" for row in live["timeline"])
        assert {row["target_ecu"] for row in live["active_fault_markers"]} == {"abs", "engine"}

        session.offline(False, "abs")
        session.clear_fault("engine-library")
        session.tick()
        session.stop()
    finally:
        session.close()

    run = tmp_path / session.id
    assert (
        saved_view(run, live["sim_time_ns"])["active_fault_markers"] == live["active_fault_markers"]
    )
    assert saved_view(run)["active_fault_markers"] == []
