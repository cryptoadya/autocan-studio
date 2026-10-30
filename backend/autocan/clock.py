"""Deterministic integer-time scheduling; wall-clock pacing belongs to a runner."""

from collections.abc import Callable
from enum import IntEnum
from heapq import heappop, heappush


class Phase(IntEnum):
    INPUT = 0
    PHYSICS = 1
    PUBLICATION = 2
    DELIVERY = 3
    DEADLINE = 4
    SNAPSHOT = 5


def validate_time(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("virtual time must be a nonnegative integer in nanoseconds")


class SimulationClock:
    def __init__(self, *, max_pending: int = 1024) -> None:
        if type(max_pending) is not int or max_pending <= 0:
            raise ValueError("max_pending must be a positive integer")
        self._max_pending = max_pending
        self._queue: list[tuple[int, Phase, int, Callable[[], None]]] = []
        self._now_ns = 0
        self._phase: Phase | None = None
        self._enqueue_seq = 0
        self._paused = False
        self._advancing = False
        self._failed = False

    @property
    def now_ns(self) -> int:
        return self._now_ns

    @property
    def paused(self) -> bool:
        return self._paused

    def can_schedule(self, at_ns: int, phase: Phase) -> bool:
        validate_time(at_ns)
        return at_ns > self._now_ns or (
            at_ns == self._now_ns and (self._phase is None or phase >= self._phase)
        )

    def schedule(self, at_ns: int, phase: Phase, callback: Callable[[], None]) -> None:
        if not isinstance(phase, Phase):
            raise ValueError("phase must be a Phase")
        if not self.can_schedule(at_ns, phase):
            raise ValueError("cannot schedule in the past or an earlier phase")
        if len(self._queue) >= self._max_pending:
            raise RuntimeError("simulation event queue is full")
        self._enqueue_seq += 1
        heappush(self._queue, (at_ns, phase, self._enqueue_seq, callback))

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def advance_to(self, target_ns: int) -> None:
        self._advance(target_ns, force=False)

    def step_to(self, target_ns: int) -> None:
        """Explicitly advance even while paused; preserve the pause setting."""
        self._advance(target_ns, force=True)

    def _advance(self, target_ns: int, *, force: bool) -> None:
        validate_time(target_ns)
        if target_ns < self._now_ns:
            raise ValueError("cannot advance backwards")
        if self._advancing or self._failed:
            raise RuntimeError("clock is advancing or failed; reset a failed clock")
        if self._paused and not force:
            return
        self._advancing = True
        try:
            while self._queue and self._queue[0][0] <= target_ns:
                if self._paused and not force:
                    return
                self._now_ns, self._phase, _, callback = heappop(self._queue)
                callback()
            if not self._paused or force:
                if target_ns > self._now_ns:
                    self._phase = None
                self._now_ns = target_ns
        except Exception:
            self._failed = True
            raise
        finally:
            self._advancing = False

    def reset(self) -> None:
        if self._advancing:
            raise RuntimeError("cannot reset inside a clock callback")
        self._queue.clear()
        self._now_ns = self._enqueue_seq = 0
        self._phase = None
        self._paused = self._failed = False
