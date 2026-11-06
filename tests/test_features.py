"""Causal windows and rules consume only the selected receiver's wire stream."""

from collections.abc import Iterator
from dataclasses import asdict
from functools import partial
from pathlib import Path

import pytest

from autocan.clock import Phase, SimulationClock
from autocan.dbc import load_democar_database
from autocan.events import CanObservation

MS = 1_000_000


def observation(at_ms: int, rpm: float = 800) -> CanObservation:
    return CanObservation("democar", 512, False, 2, int(rpm * 4).to_bytes(2, "little"), at_ms * MS)


def test_deadline_includes_delivery_at_boundary_but_never_future_and_emits_empty_windows() -> None:
    from autocan.features import FeatureConfig, FeaturePipeline, FeatureWindow

    clock = SimulationClock()
    windows: list[FeatureWindow] = []
    pipeline = FeaturePipeline(
        clock,
        profile_id="democar-network",
        receiver_id="observer",
        end_ns=1500 * MS,
        config=FeatureConfig(),
        on_window=windows.append,
    )
    for item in (observation(0), observation(500, 1000), observation(501, 7000)):
        clock.schedule(item.received_at_ns, Phase.DELIVERY, partial(pipeline.receive, item))
    clock.advance_to(1500 * MS)
    assert [w.message_count for w in windows] == [2, 1, 0]
    assert [w.end_ns for w in windows] == [500 * MS, 1000 * MS, 1500 * MS]
    assert windows[0].rpm_mean == 900
    assert windows[0].rpm_median == 900
    assert windows[0].rpm_std == 100
    assert windows[1].rpm_mean == 7000
    assert windows[2].rpm_mean is None
    engine = next(m for m in windows[0].messages if m.can_id == 512)
    assert (engine.age_ns, engine.timed_out, engine.interval_median_ns) == (0, False, 500 * MS)
    assert next(m for m in windows[2].messages if m.can_id == 512).timed_out
    # Missing traffic never becomes an observed zero RPM.
    assert windows[2].rpm_missing and windows[2].wheels_missing


def test_pause_and_exact_deadline_refresh_prevent_spurious_timeout() -> None:
    from autocan.features import FeatureConfig, FeaturePipeline, FeatureWindow

    clock = SimulationClock()
    windows: list[FeatureWindow] = []
    pipeline = FeaturePipeline(
        clock,
        profile_id="democar",
        receiver_id="KOMBI",
        end_ns=200 * MS,
        config=FeatureConfig(window_ns=100 * MS),
        on_window=windows.append,
    )
    for at in (0, 100):
        clock.schedule(at * MS, Phase.DELIVERY, partial(pipeline.receive, observation(at)))
    clock.pause()
    clock.advance_to(200 * MS)
    assert windows == []
    clock.step_to(100 * MS)
    assert windows[0].messages[0].timed_out is False
    clock.resume()
    clock.advance_to(200 * MS)
    assert windows[1].messages[0].timed_out is True


def test_three_rules_scores_and_grouped_open_recover_transitions() -> None:
    from autocan.features import BaselineDetector, FeatureConfig, FeaturePipeline, FeatureWindow

    clock = SimulationClock()
    windows: list[FeatureWindow] = []
    config = FeatureConfig(window_ns=100 * MS)
    pipeline = FeaturePipeline(
        clock,
        profile_id="democar-network",
        receiver_id="observer",
        end_ns=400 * MS,
        config=config,
        on_window=windows.append,
    )
    wheels = load_democar_database("democar-network").get_message_by_name("WheelSpeeds")
    for at, rpm, front in ((0, 800, 10), (20, 7000, 30), (220, 7000, 10), (300, 7000, 10)):
        item = observation(at, rpm)
        payload = wheels.encode(
            {"FrontLeft": front, "FrontRight": 10, "RearLeft": 10, "RearRight": 10}
        )
        wheel = CanObservation("democar", 528, False, 8, payload, at * MS)
        clock.schedule(at * MS, Phase.DELIVERY, partial(pipeline.receive, item))
        clock.schedule(at * MS, Phase.DELIVERY, partial(pipeline.receive, wheel))
    clock.advance_to(400 * MS)
    assert windows[0].rpm_derivative_max == 310000
    assert windows[0].wheel_residual_max == 20
    assert windows[1].rpm_derivative_max is None
    detector = BaselineDetector(config)
    records = [r for w in windows for r in detector.evaluate(w)]
    alerts = [r for r in records if r["kind"] == "alert"]
    assert {(r["rule_id"], r["state"]) for r in alerts} >= {
        ("heartbeat_timeout", "opened"),
        ("heartbeat_timeout", "resolved"),
        ("rpm_derivative", "opened"),
        ("rpm_derivative", "resolved"),
        ("wheel_residual", "opened"),
        ("wheel_residual", "resolved"),
    }
    assert sum(r["rule_id"] == "rpm_derivative" and r["state"] == "opened" for r in alerts) == 1
    assert all(r["feature_schema_version"] == 1 and r["detector_id"] == "tier0-1" for r in records)
    assert all("fault_id" not in asdict(w) for w in windows)


def test_reject_future_late_wrong_subscription_and_unbounded_windows() -> None:
    from autocan.features import FeatureConfig, FeaturePipeline

    clock = SimulationClock()
    pipeline = FeaturePipeline(
        clock,
        profile_id="democar-network",
        receiver_id="engine",
        end_ns=500 * MS,
        config=FeatureConfig(),
        on_window=lambda window: None,
    )
    with pytest.raises(ValueError, match="time"):
        pipeline.receive(observation(1))
    with pytest.raises(ValueError, match="subscription"):
        pipeline.receive(observation(0))
    with pytest.raises(ValueError, match="window"):
        FeaturePipeline(
            clock,
            profile_id="democar",
            receiver_id="KOMBI",
            end_ns=500 * MS,
            config=FeatureConfig(window_ns=1),
            on_window=lambda window: None,
        )


def test_baseline_cli_reads_m7_observer_dataset_and_is_deterministic(tmp_path: Path) -> None:
    import json
    import subprocess
    import sys

    from autocan.parquet import export_observer
    from autocan.scenarios import Scenario
    from autocan.traces import record_scenario

    source, dataset = tmp_path / "run", tmp_path / "observer"
    record_scenario(Scenario.load(Path("scenarios/rpm_spike.json")), source)
    export_observer(source, dataset, receiver_id="observer")
    command = [sys.executable, "-m", "autocan", "baseline", str(dataset), "--window", "0.1"]
    first = subprocess.run(command, capture_output=True, text=True, check=False)
    assert first.returncode == 0, first.stderr
    second = subprocess.run(command, capture_output=True, text=True, check=False)
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout
    records = [json.loads(line) for line in first.stdout.splitlines()]
    assert any(r["kind"] == "features" for r in records)
    assert any(r["kind"] == "alert" and r["rule_id"] == "rpm_derivative" for r in records)
    invalid = subprocess.run(
        command + ["--window", "0"], capture_output=True, text=True, check=False
    )
    assert invalid.returncode != 0 and "error:" in invalid.stderr


@pytest.mark.parametrize("delay_ms", [0, 5])
def test_live_session_windows_match_offline_during_bus_silence(
    tmp_path: Path, delay_ms: int
) -> None:
    from autocan.api.runtime import Session
    from autocan.features import FeatureConfig, FeaturePipeline, FeatureWindow, replay_observations
    from autocan.network_profile import ECU_IDS
    from autocan.parquet import export_observer, open_observer
    from autocan.scenarios import Scenario

    config = FeatureConfig()
    scenario = Scenario(
        profile_id="democar-network", duration_ns=2050 * MS, delivery_delay_ns=delay_ms * MS
    )
    session = Session(tmp_path, seed=42, profile_id=scenario.profile_id, scenario=scenario)
    try:
        session.inputs(throttle=50)
        session.start()
        for _ in range(10):
            session.tick()
        assert [window.end_ns for window in session.feature_windows] == [500 * MS]

        session.pause()
        for _ in range(12):
            session.tick()
        assert [window.end_ns for window in session.feature_windows] == [500 * MS]

        session.offline(True, "abs")
        session.start()
        for _ in range(10):
            session.tick()
        partial = session.feature_windows[-1]
        assert partial.end_ns == 1000 * MS and partial.message_count > 0
        assert next(message for message in partial.messages if message.can_id == 528).timed_out

        for ecu_id in ECU_IDS:
            if ecu_id != "abs":
                session.offline(True, ecu_id)
        for _ in range(20):
            session.tick()
        assert [window.end_ns for window in session.feature_windows] == [
            500 * MS,
            1000 * MS,
            1500 * MS,
            2000 * MS,
        ]
        silent = session.feature_windows[-1]
        assert silent.message_count == 0
        assert silent.rpm_missing and silent.wheels_missing
        assert all(message.timed_out for message in silent.messages)
        session.tick()
        assert session.stopped
    finally:
        session.close()

    dataset = tmp_path / "dataset"
    export_observer(tmp_path / session.id, dataset, receiver_id="observer")
    offline: list[FeatureWindow] = []
    with open_observer(dataset) as stream:
        clock = SimulationClock()
        pipeline = FeaturePipeline(
            clock,
            profile_id=stream.profile_id,
            receiver_id=stream.receiver_id,
            end_ns=stream.end_ns,
            config=config,
            on_window=offline.append,
        )
        first_window_receipts = 0
        boundary_receipts = 0

        def count_receipts() -> Iterator[CanObservation]:
            nonlocal first_window_receipts, boundary_receipts
            for item in stream.observations:
                first_window_receipts += item.received_at_ns <= config.window_ns
                boundary_receipts += item.received_at_ns == config.window_ns
                yield item

        replay_observations(count_receipts(), clock, pipeline, end_ns=stream.end_ns)
    assert (boundary_receipts > 0) == (delay_ms == 0)
    assert first_window_receipts > 0
    assert session.feature_windows[0].message_count == first_window_receipts
    assert session.feature_windows == offline


def test_reader_rejects_corrupted_observations_and_never_exposes_labels(tmp_path: Path) -> None:
    from autocan.parquet import export_observer, open_observer
    from autocan.scenarios import Scenario
    from autocan.traces import TraceError, record_scenario

    record_scenario(Scenario.load(Path("scenarios/rpm_spike.json")), tmp_path / "run")
    export_observer(tmp_path / "run", tmp_path / "dataset", receiver_id="observer")
    with open_observer(tmp_path / "dataset") as source:
        first = next(source.observations)
        assert set(asdict(first)) == {
            "channel",
            "can_id",
            "is_extended_id",
            "dlc",
            "payload",
            "received_at_ns",
        }
    path = tmp_path / "dataset/observations.parquet"
    path.write_bytes(path.read_bytes() + b"corrupted")
    with pytest.raises(TraceError, match="checksum|size"):
        with open_observer(tmp_path / "dataset") as source:
            list(source.observations)


def test_same_time_rpm_and_long_gap_are_missing_derivatives_and_tail_is_not_padded() -> None:
    from autocan.features import FeatureConfig, FeaturePipeline, FeatureWindow, replay_observations

    clock = SimulationClock()
    windows: list[FeatureWindow] = []
    pipeline = FeaturePipeline(
        clock,
        profile_id="democar",
        receiver_id="KOMBI",
        end_ns=250 * MS,
        config=FeatureConfig(window_ns=100 * MS),
        on_window=windows.append,
    )
    replay_observations(
        iter([observation(0), observation(0, 7000), observation(150, 800)]),
        clock,
        pipeline,
        end_ns=250 * MS,
    )
    assert len(windows) == 2
    assert all(w.rpm_derivative_max is None for w in windows)
    assert [w.rpm_missing for w in windows] == [False, False]


@pytest.mark.parametrize("option", ["--rpm-derivative-threshold", "--wheel-residual-threshold"])
def test_cli_rejects_nonfinite_thresholds(option: str, capsys: pytest.CaptureFixture[str]) -> None:
    from autocan.cli import main

    assert main(["baseline", "unused", option, "nan"]) == 1
    assert "finite" in capsys.readouterr().err


def test_never_seen_heartbeats_timeout_on_silent_bus_without_fake_signal_scores() -> None:
    from autocan.features import BaselineDetector, FeatureConfig, FeaturePipeline, FeatureWindow

    clock = SimulationClock()
    windows: list[FeatureWindow] = []
    config = FeatureConfig(window_ns=100 * MS)
    FeaturePipeline(
        clock,
        profile_id="democar-network",
        receiver_id="observer",
        end_ns=600 * MS,
        config=config,
        on_window=windows.append,
    )
    clock.advance_to(600 * MS)
    assert len(windows) == 6 and all(w.message_count == 0 for w in windows)
    detector = BaselineDetector(config)
    records = [r for w in windows for r in detector.evaluate(w)]
    alerts = [r for r in records if r["kind"] == "alert"]
    assert len(alerts) == 8  # one opening per expected CAN ID, never one per empty window
    assert {r["sim_time_ns"] for r in alerts} == {100 * MS, 500 * MS}
    assert all(r["rule_id"] == "heartbeat_timeout" and r["state"] == "opened" for r in alerts)
    assert all(r["score"] is None for r in records if r["rule_id"] != "heartbeat_timeout")


def test_reject_late_delivery_and_observation_overflow(monkeypatch: pytest.MonkeyPatch) -> None:
    from autocan.features import FeatureConfig, FeaturePipeline

    monkeypatch.setattr("autocan.features.MAX_WINDOW_OBSERVATIONS", 2)
    clock = SimulationClock()
    pipeline = FeaturePipeline(
        clock,
        profile_id="democar",
        receiver_id="KOMBI",
        end_ns=100 * MS,
        config=FeatureConfig(window_ns=100 * MS),
        on_window=lambda window: None,
    )
    pipeline.receive(observation(0))
    pipeline.receive(observation(0))
    with pytest.raises(ValueError, match="quota"):
        pipeline.receive(observation(0))
    clock.advance_to(100 * MS)
    with pytest.raises(ValueError, match="time"):
        pipeline.receive(observation(100))


def test_rule_threshold_equality_is_normal_and_rpm_difference_crosses_windows() -> None:
    from autocan.features import (
        BaselineDetector,
        FeatureConfig,
        FeaturePipeline,
        FeatureWindow,
        replay_observations,
    )

    clock = SimulationClock()
    config = FeatureConfig(window_ns=100 * MS)
    windows: list[FeatureWindow] = []
    pipeline = FeaturePipeline(
        clock,
        profile_id="democar",
        receiver_id="KOMBI",
        end_ns=200 * MS,
        config=config,
        on_window=windows.append,
    )
    replay_observations(
        iter([observation(90, 800), observation(110, 1200)]), clock, pipeline, end_ns=200 * MS
    )
    assert windows[0].rpm_derivative_max is None
    assert windows[1].rpm_derivative_max == 20_000
    detector = BaselineDetector(config)
    assert not any(r["kind"] == "alert" for w in windows for r in detector.evaluate(w))


@pytest.mark.parametrize("field", ["profile_version", "profile_sha256", "final_state"])
def test_cli_reports_missing_observer_context_as_input_error(
    tmp_path: Path, field: str, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from autocan.cli import main
    from autocan.parquet import export_observer
    from autocan.scenarios import Scenario
    from autocan.traces import record_scenario

    record_scenario(Scenario(duration_ns=100 * MS), tmp_path / "run")
    export_observer(tmp_path / "run", tmp_path / "dataset")
    path = tmp_path / "dataset/manifest.json"
    manifest = json.loads(path.read_text())
    del manifest["trace_manifest"][field]
    path.write_text(json.dumps(manifest))
    assert main(["baseline", str(tmp_path / "dataset")]) == 1
    assert "error:" in capsys.readouterr().err
