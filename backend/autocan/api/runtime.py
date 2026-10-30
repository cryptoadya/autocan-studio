"""Single-process live pacing and bounded views of the existing core/trace stream."""

from contextlib import suppress
from pathlib import Path
from typing import Any

from cantools.database.can import Database

from autocan.api.observation import FRAME_LIMIT as FRAME_LIMIT
from autocan.api.observation import SIGNAL_LIMIT as SIGNAL_LIMIT
from autocan.api.observation import ObservationView as ObservationView
from autocan.api.observation import make_view
from autocan.dbc import profile_from_version
from autocan.events import RunEvent
from autocan.simulation import Simulation
from autocan.traces import TraceRecorder, playback, verify_trace
from autocan.validation import integer
from autocan.vehicle import validate_brake, validate_gear, validate_ignition, validate_throttle

TICK_NS = 50_000_000
MAX_DURATION_NS = 120_000_000_000
MAX_RUNS = 64
MAX_COMMANDS = 256


class Session:
    def __init__(self, root: Path, *, seed: int, profile_id: str = "democar") -> None:
        root.mkdir(parents=True, exist_ok=True)
        if sum(1 for _ in root.iterdir()) >= MAX_RUNS:
            raise ValueError("run quota reached (64); archive local runs before creating more")
        self.view = make_view(profile_id)
        self.stopped = False
        self.error: str | None = None
        self.commands = 0
        self.sim = Simulation(seed=seed, profile_id=profile_id, on_event=self._event)
        self.id = self.sim.run_id
        try:
            self.recorder = TraceRecorder(root / self.id)
            self.recorder.begin(self.sim)
            self.sim.pause()
        except Exception:
            self.sim.close()
            raise

    def _event(self, event: RunEvent) -> None:
        self.recorder.write(event)
        self.view.consume(event.as_dict())

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

    def pause(self) -> None:
        self._check_active()
        self.sim.pause()

    def tick(self) -> None:
        if self.stopped or self.sim.state()["paused"]:
            return
        now = int(self.sim.state()["sim_time_ns"] or 0)
        self.sim.advance_to(min(now + TICK_NS, MAX_DURATION_NS))
        self.view.sample(int(self.sim.state()["sim_time_ns"] or 0))
        if now + TICK_NS >= MAX_DURATION_NS:
            self.stop()

    def stop(self) -> None:
        if self.stopped:
            return
        # Flush initial events for a run saved before its first pacing tick.
        if not self.sim.state()["events_emitted"]:
            self.sim.step()
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
    meta = verify_trace(path)
    end = integer(meta["end_time_ns"], "end time")
    target = end if until_ns is None else min(until_ns, end)
    database = Database(strict=True)
    database.add_dbc_string((path / "profile.dbc").read_text())
    view = make_view(profile_from_version(meta["profile_version"]), database)
    # Only retain the final display window, even for a long CLI recording.
    first_sample = max(0, target - (SIGNAL_LIMIT - 1) * TICK_NS)
    next_sample = ((first_sample + TICK_NS - 1) // TICK_NS) * TICK_NS
    for event in playback(path):
        event_time = integer(event["sim_time_ns"], "event time")
        if event_time > target:
            break
        while next_sample < event_time:
            view.sample(next_sample)
            next_sample += TICK_NS
        view.consume(event)
    while next_sample <= target:
        view.sample(next_sample)
        next_sample += TICK_NS
    view.sample(target)
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
    }
