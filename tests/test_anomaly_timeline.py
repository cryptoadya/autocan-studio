"""Saved score points and causal alert episodes survive playback and trace exchange."""

from pathlib import Path

from autocan.api.runtime import saved_view
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
