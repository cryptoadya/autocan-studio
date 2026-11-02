"""Causal, bounded Tier 0 features. Detector inputs contain no simulator truth."""

from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from statistics import fmean, median, pstdev
from typing import cast

from autocan.clock import Phase, SimulationClock, validate_time
from autocan.dbc import load_democar_database
from autocan.events import CanObservation
from autocan.network_profile import PLAN_BY_ID, RECEIVERS
from autocan.validation import integer, number

MAX_WINDOWS = 10_000
MAX_WINDOW_OBSERVATIONS = 20_000
FEATURE_SCHEMA_VERSION = 1
DETECTOR_ID = "tier0-1"


@dataclass(frozen=True, slots=True)
class FeatureConfig:
    window_ns: int = 500_000_000
    heartbeat_periods: int = 5
    rpm_derivative_threshold: float = 20_000
    wheel_residual_threshold: float = 5

    def __post_init__(self) -> None:
        integer(self.window_ns, "window_ns", minimum=1)
        integer(self.heartbeat_periods, "heartbeat_periods", minimum=1)
        number(self.rpm_derivative_threshold, "RPM derivative threshold", minimum=0, maximum=1e12)
        number(self.wheel_residual_threshold, "wheel residual threshold", minimum=0, maximum=360)


@dataclass(frozen=True, slots=True)
class MessageFeatures:
    can_id: int
    count: int
    age_ns: int
    timeout_ns: int
    timed_out: bool
    interval_min_ns: int | None
    interval_median_ns: float | None
    interval_max_ns: int | None
    interval_jitter_ns: float | None


@dataclass(frozen=True, slots=True)
class FeatureWindow:
    start_ns: int
    end_ns: int
    message_count: int
    messages: tuple[MessageFeatures, ...]
    rpm_mean: float | None
    rpm_median: float | None
    rpm_std: float | None
    rpm_derivative_max: float | None
    wheel_residual_max: float | None
    rpm_missing: bool
    wheels_missing: bool
    feature_schema_version: int = FEATURE_SCHEMA_VERSION


@dataclass(slots=True)
class _Accumulator:
    count: int = 0
    intervals: list[int] = field(default_factory=list)


class FeaturePipeline:
    """First window [0, W], then (kW, (k+1)W]; delivery precedes deadline.

    Attach to a fresh clock, feed only observations during DELIVERY, and advance
    that clock even when the bus is silent. Incomplete trailing windows are omitted.
    """

    def __init__(
        self,
        clock: SimulationClock,
        *,
        profile_id: str,
        receiver_id: str,
        end_ns: int,
        config: FeatureConfig,
        on_window: Callable[[FeatureWindow], None],
    ) -> None:
        validate_time(end_ns)
        if clock.now_ns != 0:
            raise ValueError("feature pipeline requires a fresh clock")
        if end_ns // config.window_ns > MAX_WINDOWS:
            raise ValueError("feature window quota exceeded")
        self._db = load_democar_database(profile_id)
        if receiver_id not in (RECEIVERS if profile_id == "democar-network" else ("KOMBI",)):
            raise ValueError("unsupported receiver for this profile")
        self._periods = {
            message.frame_id: int(message.cycle_time or 20) * 1_000_000
            for message in self._db.messages
            if profile_id == "democar" or receiver_id in PLAN_BY_ID[message.frame_id].receivers
        }
        self._timeouts = {
            can_id: max(100_000_000, period * config.heartbeat_periods)
            for can_id, period in self._periods.items()
        }
        self._clock, self._config, self._end_ns = clock, config, end_ns
        self._on_window = on_window
        self._start_ns = 0
        self._closed_at_ns = -1
        self._last: dict[int, int] = {}
        self._previous_rpm: tuple[int, float] | None = None
        self._channel: str | None = None
        self._reset_window()
        if config.window_ns <= end_ns:
            clock.schedule(config.window_ns, Phase.DEADLINE, self._close_window)

    def _reset_window(self) -> None:
        self._messages = {can_id: _Accumulator() for can_id in self._periods}
        self._count = 0
        self._rpms: list[float] = []
        self._derivatives: list[float] = []
        self._residuals: list[float] = []

    def receive(self, observation: CanObservation) -> None:
        at = observation.received_at_ns
        if at != self._clock.now_ns or at <= self._closed_at_ns or at > self._end_ns:
            raise ValueError("observation time must equal current clock time before window closure")
        if observation.can_id not in self._periods:
            raise ValueError("observation is outside the selected receiver subscription")
        if self._channel is not None and observation.channel != self._channel:
            raise ValueError("a feature stream must use a single CAN channel")
        if self._count >= MAX_WINDOW_OBSERVATIONS:
            raise ValueError("feature window observation quota exceeded")
        message = self._db.get_message_by_frame_id(observation.can_id)
        if observation.dlc != message.length:
            raise ValueError("observation DLC does not match profile")
        values = cast(dict[str, float], message.decode(observation.payload, decode_choices=False))
        self._channel = observation.channel
        accumulator = self._messages[observation.can_id]
        previous = self._last.get(observation.can_id)
        if previous is not None:
            accumulator.intervals.append(at - previous)
        self._last[observation.can_id] = at
        accumulator.count += 1
        self._count += 1
        if message.name == "EngineStatus":
            rpm = float(values["EngineRpm"])
            self._rpms.append(rpm)
            if self._previous_rpm is not None:
                before, value = self._previous_rpm
                gap = at - before
                if 0 < gap <= self._timeouts[observation.can_id]:
                    self._derivatives.append(abs(rpm - value) * 1_000_000_000 / gap)
            self._previous_rpm = (at, rpm)
        elif message.name == "WheelSpeeds":
            wheels = [
                float(values[name]) for name in ("FrontLeft", "FrontRight", "RearLeft", "RearRight")
            ]
            reference = median(wheels)
            self._residuals.append(max(abs(value - reference) for value in wheels))

    def _close_window(self) -> None:
        end = self._clock.now_ns
        messages = []
        for can_id, accumulator in self._messages.items():
            intervals = accumulator.intervals
            age = end - self._last.get(can_id, 0)
            timeout = self._timeouts[can_id]
            messages.append(
                MessageFeatures(
                    can_id,
                    accumulator.count,
                    age,
                    timeout,
                    age >= timeout,
                    min(intervals) if intervals else None,
                    float(median(intervals)) if intervals else None,
                    max(intervals) if intervals else None,
                    pstdev(intervals) if intervals else None,
                )
            )
        window = FeatureWindow(
            self._start_ns,
            end,
            self._count,
            tuple(messages),
            fmean(self._rpms) if self._rpms else None,
            float(median(self._rpms)) if self._rpms else None,
            pstdev(self._rpms) if self._rpms else None,
            max(self._derivatives) if self._derivatives else None,
            max(self._residuals) if self._residuals else None,
            not self._rpms,
            not self._residuals,
        )
        self._closed_at_ns = self._start_ns = end
        self._reset_window()
        self._on_window(window)
        following = end + self._config.window_ns
        if following <= self._end_ns:
            self._clock.schedule(following, Phase.DEADLINE, self._close_window)


class BaselineDetector:
    """One open alert per rule/message; unavailable evidence never clears it."""

    def __init__(self, config: FeatureConfig) -> None:
        self._config = config
        self._active: set[tuple[str, int]] = set()

    def evaluate(self, window: FeatureWindow) -> Iterator[dict[str, object]]:
        scores: list[tuple[str, int, float | None, float, bool | None]] = [
            ("heartbeat_timeout", m.can_id, float(m.age_ns), float(m.timeout_ns), m.timed_out)
            for m in window.messages
        ]
        for rule, can_id, score, threshold in (
            (
                "rpm_derivative",
                512,
                window.rpm_derivative_max,
                self._config.rpm_derivative_threshold,
            ),
            (
                "wheel_residual",
                528,
                window.wheel_residual_max,
                self._config.wheel_residual_threshold,
            ),
        ):
            scores.append(
                (rule, can_id, score, threshold, None if score is None else score > threshold)
            )
        for rule, can_id, score, threshold, anomalous in scores:
            record: dict[str, object] = {
                "kind": "score",
                "rule_id": rule,
                "can_id": can_id,
                "sim_time_ns": window.end_ns,
                "score": score,
                "threshold": threshold,
                "anomalous": anomalous,
                "detector_id": DETECTOR_ID,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
            }
            yield record
            key = (rule, can_id)
            active = key in self._active
            if anomalous is not None and anomalous != active:
                if anomalous:
                    self._active.add(key)
                else:
                    self._active.remove(key)
                yield {**record, "kind": "alert", "state": "opened" if anomalous else "resolved"}


def replay_observations(
    observations: Iterator[CanObservation],
    clock: SimulationClock,
    pipeline: FeaturePipeline,
    *,
    end_ns: int,
) -> None:
    """Schedule one receipt ahead, retaining stable equal-time input ordering."""

    def enqueue_next() -> None:
        item = next(observations, None)
        if item is None:
            return
        if item.received_at_ns > end_ns:
            raise ValueError("observation time exceeds run end")

        def deliver() -> None:
            pipeline.receive(item)
            enqueue_next()

        clock.schedule(item.received_at_ns, Phase.DELIVERY, deliver)

    enqueue_next()
    clock.advance_to(end_ns)


def feature_record(window: FeatureWindow) -> dict[str, object]:
    return {"kind": "features", **asdict(window)}
