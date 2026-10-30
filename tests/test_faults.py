"""Offline is publication loss; virtual freshness timers need no bus traffic."""

from autocan.clock import Phase, SimulationClock
from autocan.ecu import Kombi, KombiState
from autocan.events import CanObservation, RunEvent
from autocan.simulation import Simulation


def test_offline_keeps_engine_running_and_reaches_silent_bus_deadlines() -> None:
    events: list[RunEvent] = []
    with Simulation(on_event=events.append) as sim:
        sim.set_throttle(50)
        sim.set_engine_publishing(False, at_ns=100_000_000)
        sim.set_engine_publishing(True, at_ns=400_000_000)
        sim.advance_to(139_999_999)
        assert sim.state()["kombi_status"] == "FRESH"
        sim.advance_to(140_000_000)
        assert sim.state()["kombi_status"] == "STALE"
        sim.advance_to(280_000_000)
        assert sim.state()["kombi_status"] == "TIMEOUT"
        assert sim.state()["kombi_rpm"] == 960
        assert sim.state()["physical_rpm"] == 1360
        sim.advance_to(400_000_000)
        assert sim.state()["kombi_status"] == "FRESH"
        assert sim.state()["kombi_rpm"] == 1600
        assert sim.state()["engine_running"] is True
    status = [
        (e.sim_time_ns, e.data["status"]) for e in events if e.event_kind == "EcuStatusChanged"
    ]
    assert status == [
        (0, "FRESH"),
        (140_000_000, "STALE"),
        (280_000_000, "TIMEOUT"),
        (400_000_000, "FRESH"),
    ]
    assert not any(
        e.event_kind == "FramePublished" and 100_000_000 <= e.sim_time_ns < 400_000_000
        for e in events
    )


def test_offline_preserves_previously_published_delayed_frame() -> None:
    events: list[RunEvent] = []
    with Simulation(delivery_delay_ns=60_000_000, on_event=events.append) as sim:
        sim.set_throttle(50)
        sim.set_engine_publishing(False, at_ns=20_000_000)
        sim.advance_to(59_999_999)
        assert sim.state()["kombi_status"] == "UNAVAILABLE"
        sim.advance_to(60_000_000)
        assert sim.state()["physical_rpm"] == 920
        assert sim.state()["kombi_rpm"] == 800
        sim.advance_to(260_000_000)
        assert sim.state()["kombi_status"] == "TIMEOUT"
        assert sim.state()["frames_published"] == sim.state()["deliveries"] == 1
    assert next(e for e in events if e.event_kind == "FramePublished").sim_time_ns == 0
    assert next(e for e in events if e.event_kind == "FrameReceived").sim_time_ns == 60_000_000


def test_delivery_at_deadline_precedes_freshness_and_old_timer_is_ignored() -> None:
    clock = SimulationClock()
    kombi = Kombi(clock=clock)
    kombi.receive(CanObservation("democar", 0x200, False, 2, b"\x80\x0c", 0))

    def deliver() -> None:
        kombi.receive(CanObservation("democar", 0x200, False, 2, b"\x80\x0c", 60_000_000))

    clock.schedule(60_000_000, Phase.DELIVERY, deliver)
    clock.advance_to(60_000_000)
    assert kombi.state == KombiState(800, 60_000_000, "FRESH")
    clock.advance_to(200_000_000)
    assert kombi.state == KombiState(800, 60_000_000, "STALE")
    clock.advance_to(260_000_000)
    assert kombi.state == KombiState(800, 60_000_000, "TIMEOUT")


def test_seeded_drop_is_repeatable_and_does_not_change_physics() -> None:
    received: list[list[int]] = []
    physical: list[float | int | str | bool | None] = []
    for probability in (0.0, 0.5, 0.5):
        events: list[RunEvent] = []
        with Simulation(seed=42, drop_probability=probability, on_event=events.append) as sim:
            sim.set_throttle(50)
            sim.advance_to(1_000_000_000)
            physical.append(sim.state()["physical_rpm"])
        received.append([e.sim_time_ns for e in events if e.event_kind == "FrameReceived"])
    assert physical == [2800, 2800, 2800]
    assert received[1] == received[2]
    assert 0 < len(received[1]) < len(received[0]) == 51


def test_reset_clears_offline_state_and_reseeds_delivery_randomness() -> None:
    with Simulation(seed=42, drop_probability=1) as sim:
        sim.set_engine_publishing(False)
        sim.advance_to(200_000_000)
        sim.reset()
        assert sim.state()["engine_publishing"] is True
        assert sim.state()["kombi_status"] == "UNAVAILABLE"
        sim.advance_to(100_000_000)
        assert sim.state()["frames_published"] == 6
        assert sim.state()["deliveries"] == 0
