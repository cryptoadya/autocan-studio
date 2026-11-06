"""Single-process live pacing and bounded views of the existing core/trace stream."""

from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cantools.database.can import Database

from autocan.api.live_features import LiveFeatureBus
from autocan.api.observation import FRAME_LIMIT as FRAME_LIMIT
from autocan.api.observation import SIGNAL_LIMIT as SIGNAL_LIMIT
from autocan.api.observation import ObservationView as ObservationView
from autocan.api.observation import make_view
from autocan.api.timeline import AnomalyTimeline, FaultTimeline
from autocan.dbc import profile_from_version
from autocan.events import RunEvent
from autocan.faults import FaultSpec, fault_id
from autocan.features import FeatureConfig, FeatureWindow
from autocan.scenarios import Scenario
from autocan.simulation import Simulation
from autocan.traces import (
    MAX_TRACE_BYTES,
    ScoreSample,
    TraceRecorder,
    open_playback,
    recorded_scores,
)
from autocan.validation import integer, object_value
from autocan.vehicle import validate_brake, validate_gear, validate_ignition, validate_throttle

if TYPE_CHECKING:
    from autocan.live_scoring import LiveModel
    from autocan.window_evaluation import WindowScore

TICK_NS = 50_000_000
MAX_DURATION_NS = 120_000_000_000
MAX_RUNS = 64
MAX_COMMANDS = 256


class Session:
    def __init__(
        self,
        root: Path,
        *,
        seed: int,
        profile_id: str = "democar",
        scenario: Scenario | None = None,
        scenario_info: dict[str, object] | None = None,
        live_model: "LiveModel | None" = None,
    ) -> None:
        if scenario is not None and (
            scenario.duration_ns > MAX_DURATION_NS or scenario.profile_id != profile_id
        ):
            raise ValueError("scenario exceeds the duration limit or uses a different profile")
        root.mkdir(parents=True, exist_ok=True)
        if sum(1 for _ in root.iterdir()) >= MAX_RUNS:
            raise ValueError("run quota reached (64); archive local runs before creating more")
        self.view = make_view(profile_id)
        self.timeline = FaultTimeline()
        self.scenario = scenario
        self.scenario_info = scenario_info
        self.duration_ns = scenario.duration_ns if scenario is not None else MAX_DURATION_NS
        self.stopped = False
        self.error: str | None = None
        self.commands = len(scenario.commands) if scenario is not None else 0
        model = live_model if profile_id == "democar-network" else None
        self.anomaly = AnomalyTimeline(dict(model.provenance)) if model else None
        self.feature_windows: list[FeatureWindow] = []
        self.live_scores: list[WindowScore] = []
        self._feature_bus: LiveFeatureBus | None = None

        def on_window(window: FeatureWindow) -> None:
            if model is not None:
                score = model.score(window, run_id=self.id, row_index=len(self.feature_windows))
                assert score.score is not None and self.anomaly is not None
                sample: ScoreSample = {
                    "sim_time_ns": score.key.end_ns,
                    "score": score.score,
                    "eligible": score.key.eligible,
                    "timed_out_can_ids": [
                        message.can_id for message in window.messages if message.timed_out
                    ],
                    "rpm_derivative_max": window.rpm_derivative_max,
                    "wheel_residual_max": window.wheel_residual_max,
                }
                self.recorder.write_score(sample)
                self.anomaly.consume(sample)
                self.live_scores.append(score)
            self.feature_windows.append(window)

        def feature_bus() -> LiveFeatureBus:
            config = model.configuration if model is not None else FeatureConfig()
            bus = LiveFeatureBus(MAX_DURATION_NS, config, on_window)
            self._feature_bus = bus
            return bus

        self.sim = Simulation(
            seed=seed,
            profile_id=profile_id,
            on_event=self._event,
            bus_factory=feature_bus if profile_id == "democar-network" else None,
            delivery_delay_ns=scenario.delivery_delay_ns if scenario else 0,
            drop_probability=scenario.drop_probability if scenario else 0,
        )
        self.id = self.sim.run_id
        try:
            self.recorder = TraceRecorder(root / self.id, max_bytes=MAX_TRACE_BYTES)
            self.recorder.begin(
                self.sim, scenario=scenario_info, live_model=model.provenance if model else None
            )
            if scenario is not None:
                for command in scenario.commands:
                    command.schedule(self.sim)
            self.sim.pause()
        except Exception:
            self.sim.close()
            raise

    def _event(self, event: RunEvent) -> None:
        self.recorder.write(event)
        self.view.consume(event.as_dict())
        self.timeline.consume(event.as_dict())

    def _check_active(self) -> None:
        if self.stopped:
            raise ValueError("run is stopped; create a new recording")

    def _command(self, count: int = 1) -> None:
        self._check_active()
        if self.commands + count > MAX_COMMANDS:
            raise ValueError("command quota reached (256); stop and save this run")
        self.commands += count

    def inputs(
        self,
        throttle: float | None = None,
        *,
        brake: float | None = None,
        gear: str | None = None,
        ignition: str | None = None,
        engine_running: bool | None = None,
    ) -> int:
        count = sum(
            value is not None for value in (throttle, brake, gear, ignition, engine_running)
        )
        if not count:
            raise ValueError("at least one input is required")
        if throttle is not None:
            validate_throttle(throttle)
        if brake is not None:
            validate_brake(brake)
        if gear is not None:
            validate_gear(gear)
        if ignition is not None:
            validate_ignition(ignition)
        if engine_running is not None and type(engine_running) is not bool:
            raise ValueError("engine_running must be a boolean")
        self._command(count)
        # A batch shares one input boundary; reserve its entire quota first.
        applied = 0
        if throttle is not None:
            applied = self.sim.set_throttle(throttle)
        if brake is not None:
            applied = self.sim.set_brake(brake)
        if gear is not None:
            applied = self.sim.set_gear(gear)
        if ignition is not None:
            applied = self.sim.set_ignition(ignition)
        if engine_running is not None:
            applied = self.sim.set_engine_running(engine_running)
        return applied

    def offline(self, enabled: bool, ecu_id: str = "engine") -> int:
        if ecu_id != "engine" and self.sim.profile_id != "democar-network":
            raise ValueError("additional ECU require the network profile")
        self._command()
        return (
            self.sim.set_engine_publishing(not enabled)
            if ecu_id == "engine"
            else self.sim.set_ecu_publishing(ecu_id, not enabled)
        )

    def start(self) -> None:
        self._check_active()
        self.sim.resume()

    def inject_fault(self, spec: FaultSpec) -> int:
        if self.sim.profile_id != "democar-network":
            raise ValueError("fault library requires the network profile")
        spec = FaultSpec.from_dict(spec.as_dict())
        self._command()
        return self.sim.inject_fault(spec)

    def clear_fault(self, identifier: str) -> int:
        if self.sim.profile_id != "democar-network":
            raise ValueError("fault library requires the network profile")
        fault_id(identifier)
        self._command()
        return self.sim.clear_fault(identifier)

    def pause(self) -> None:
        self._check_active()
        self.sim.pause()

    def tick(self) -> None:
        if self.stopped or self.sim.state()["paused"]:
            return
        now = int(self.sim.state()["sim_time_ns"] or 0)
        self.sim.advance_to(min(now + TICK_NS, self.duration_ns))
        current = int(self.sim.state()["sim_time_ns"] or 0)
        if self._feature_bus is not None:
            self._feature_bus.advance_features_to(current)
        self.view.sample(current)
        if now + TICK_NS >= self.duration_ns:
            self.stop()

    def stop(self) -> None:
        if self.stopped:
            return
        # Flush initial events for a run saved before its first pacing tick.
        if not self.sim.state()["events_emitted"]:
            self.sim.step()
            if self._feature_bus is not None:
                self._feature_bus.advance_features_to(int(self.sim.state()["sim_time_ns"] or 0))
        self.sim.pause()
        self.recorder.finish(self.sim.state())
        self.stopped = True

    def fail(self, error: Exception) -> None:
        self.error = str(error)
        self.stopped = True
        # A full/read-only disk may also reject the failure manifest. The old
        # incomplete manifest remains unloadable; the live session must still stop.
        with suppress(OSError):
            self.recorder.__exit__(type(error), error, error.__traceback__)
        self.sim.close()

    def snapshot(self) -> dict[str, Any]:
        state = self.sim.state()
        # Dashboard uses KOMBI values; model truth never enters this API view.
        return {
            "id": self.id,
            "mode": "live",
            "sim_time_ns": state["sim_time_ns"],
            "state": {
                k: v for k, v in state.items() if k not in {"physical_rpm", "physical_coolant_c"}
            },
            "frames": list(self.view.frames),
            "signals": list(self.view.signals),
            "stopped": self.stopped,
            "error": self.error,
            "recording": "failed" if self.error else "saved" if self.stopped else "recording",
            "scenario": self.scenario_info,
            "anomaly": self.anomaly.snapshot() if self.anomaly else None,
            **self.timeline.snapshot(),
        }

    def close(self) -> None:
        try:
            if not self.stopped:
                self.stop()
        except Exception as exc:
            self.fail(exc)
        finally:
            self.sim.close()


def saved_view(path: Path, until_ns: int | None = None) -> dict[str, Any]:
    meta, events = open_playback(path)
    end = integer(meta["end_time_ns"], "end time")
    target = end if until_ns is None else min(until_ns, end)
    database = Database(strict=True)
    database.add_dbc_string((path / "profile.dbc").read_text())
    view = make_view(profile_from_version(meta["profile_version"]), database)
    timeline = FaultTimeline()
    anomaly = None
    if "live_model" in meta:
        anomaly = AnomalyTimeline(object_value(meta["live_model"], "live model provenance"))
        for sample in recorded_scores(path, meta):
            if sample["sim_time_ns"] > target:
                break
            anomaly.consume(sample)
    # Only retain the final display window, even for a long CLI recording.
    first_sample = max(0, target - (SIGNAL_LIMIT - 1) * TICK_NS)
    next_sample = ((first_sample + TICK_NS - 1) // TICK_NS) * TICK_NS
    for event in events:
        event_time = integer(event["sim_time_ns"], "event time")
        if event_time > target:
            break
        while next_sample < event_time:
            view.sample(next_sample)
            next_sample += TICK_NS
        view.consume(event)
        timeline.consume(event)
    while next_sample <= target:
        view.sample(next_sample)
        next_sample += TICK_NS
    view.sample(target)
    view.state["active_fault_count"] = len(timeline.active)
    return {
        "id": path.name,
        "mode": "playback",
        "sim_time_ns": target,
        "duration_ns": end,
        "state": view.state,
        "frames": list(view.frames),
        "signals": list(view.signals),
        "stopped": True,
        "error": None,
        "recording": "saved",
        "scenario": meta.get("scenario"),
        "anomaly": anomaly.snapshot() if anomaly else None,
        **timeline.snapshot(),
    }
