"""Virtual-time ordering and runner controls, without sleeps or OS clocks."""

import pytest


def test_equal_time_uses_phase_then_enqueue_order() -> None:
    from autocan.clock import Phase, SimulationClock

    clock = SimulationClock()
    seen: list[str] = []
    clock.schedule(20, Phase.DELIVERY, lambda: seen.append("delivery"))
    clock.schedule(20, Phase.INPUT, lambda: seen.append("input-1"))
    clock.schedule(20, Phase.PHYSICS, lambda: seen.append("physics"))
    clock.schedule(20, Phase.INPUT, lambda: seen.append("input-2"))
    clock.advance_to(20)
    assert seen == ["input-1", "input-2", "physics", "delivery"]
    assert clock.now_ns == 20


def test_callback_cannot_schedule_past_or_earlier_phase() -> None:
    from autocan.clock import Phase, SimulationClock

    clock = SimulationClock()

    def callback() -> None:
        with pytest.raises(ValueError):
            clock.schedule(9, Phase.DELIVERY, lambda: None)
        with pytest.raises(ValueError):
            clock.schedule(10, Phase.INPUT, lambda: None)

    clock.schedule(10, Phase.DELIVERY, callback)
    clock.advance_to(10)
    with pytest.raises(ValueError):
        clock.advance_to(9)


def test_pause_step_resume_and_reset() -> None:
    from autocan.clock import Phase, SimulationClock

    clock = SimulationClock()
    seen: list[int] = []
    clock.schedule(10, Phase.PHYSICS, lambda: seen.append(clock.now_ns))
    clock.pause()
    clock.advance_to(20)
    assert (clock.now_ns, seen) == (0, [])
    clock.step_to(10)
    assert (clock.now_ns, clock.paused, seen) == (10, True, [10])
    clock.resume()
    clock.advance_to(20)
    assert clock.now_ns == 20
    clock.schedule(30, Phase.PHYSICS, lambda: seen.append(30))
    clock.reset()
    clock.advance_to(40)
    assert seen == [10]


def test_pause_inside_callback_stops_advance_at_that_time() -> None:
    from autocan.clock import Phase, SimulationClock

    clock = SimulationClock()
    clock.schedule(5, Phase.INPUT, clock.pause)
    clock.advance_to(100)
    assert clock.now_ns == 5
    assert clock.paused


@pytest.mark.parametrize("time_ns", [-1, 1.5, True])
def test_rejects_invalid_virtual_time(time_ns: object) -> None:
    from autocan.clock import Phase, SimulationClock

    clock = SimulationClock()
    with pytest.raises(ValueError):
        clock.schedule(time_ns, Phase.PHYSICS, lambda: None)  # type: ignore[arg-type]


def test_queue_limit_fails_explicitly() -> None:
    from autocan.clock import Phase, SimulationClock

    clock = SimulationClock(max_pending=1)
    clock.schedule(1, Phase.INPUT, lambda: None)
    with pytest.raises(RuntimeError, match="queue"):
        clock.schedule(2, Phase.INPUT, lambda: None)
