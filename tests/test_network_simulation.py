import pytest

from autocan.events import RunEvent
from autocan.simulation import Simulation


def prepare_drive(sim: Simulation) -> None:
    sim.set_brake(100, at_ns=0)
    sim.set_gear("D", at_ns=0)
    sim.set_throttle(50, at_ns=100_000_000)
    sim.set_brake(0, at_ns=100_000_000)


def test_five_ecu_publish_receive_and_kombi_reads_network_signals() -> None:
    events: list[RunEvent] = []
    with Simulation(profile_id="democar-network", on_event=events.append) as sim:
        prepare_drive(sim)
        sim.advance_to(1_000_000_000)
        state = sim.state()
        assert state["gear"] == state["kombi_gear"] == "D"
        assert state["kombi_speed_status"] == state["kombi_temperature_status"] == "FRESH"
        assert state["kombi_temperature_c"] == pytest.approx(20.2)
        assert state["kombi_ignition"] == "ON"
        assert state["kombi_wheels_consistent"] is True
        assert isinstance(state["kombi_speed_kph"], (float, int))
        assert state["kombi_speed_kph"] > 0
    assert {e.data["source_ecu"] for e in events if e.event_kind == "FramePublished"} == {
        "engine",
        "abs",
        "transmission",
        "body",
        "KOMBI",
    }
    assert {e.data["receiver_id"] for e in events if e.event_kind == "FrameReceived"} == {
        "engine",
        "abs",
        "transmission",
        "body",
        "KOMBI",
        "observer",
    }


def test_engine_offline_deadlines_are_independent_of_other_bus_traffic() -> None:
    with Simulation(profile_id="democar-network") as sim:
        prepare_drive(sim)
        sim.set_engine_publishing(False, at_ns=100_000_000)
        sim.advance_to(139_999_999)
        assert sim.state()["kombi_status"] == "FRESH"
        sim.advance_to(140_000_000)
        assert sim.state()["kombi_status"] == "STALE"
        sim.advance_to(280_000_000)
        assert sim.state()["kombi_status"] == "TIMEOUT"
        assert sim.state()["kombi_speed_status"] == "FRESH"
        assert sim.state()["engine_running"] is True
        sim.advance_to(1_000_000_000)
        assert sim.state()["kombi_temperature_status"] == "TIMEOUT"


def test_abs_offline_retains_can_speed_without_substituting_model_speed() -> None:
    with Simulation(profile_id="democar-network") as sim:
        prepare_drive(sim)
        sim.set_ecu_publishing("abs", False, at_ns=100_000_000)
        sim.advance_to(100_000_000)
        held = sim.state()["kombi_speed_kph"]
        sim.advance_to(300_000_000)
        assert sim.state()["kombi_speed_kph"] == held
        assert sim.state()["kombi_speed_status"] == "TIMEOUT"
        assert sim.state()["kombi_status"] == "FRESH"
        assert sim.state()["physical_speed_kph"] != held


def test_network_timers_run_even_if_no_ecu_ever_publishes() -> None:
    with Simulation(profile_id="democar-network") as sim:
        for ecu in ("engine", "abs", "transmission", "body", "KOMBI"):
            sim.set_ecu_publishing(ecu, False, at_ns=0)
        sim.advance_to(1_000_000_000)
        assert sim.state()["frames_published"] == 0
        assert sim.state()["kombi_status"] == sim.state()["kombi_speed_status"] == "TIMEOUT"
        assert sim.state()["kombi_temperature_status"] == "TIMEOUT"
        assert sim.state()["kombi_rpm"] is None


def test_network_gear_application_depends_on_delivered_can_requests() -> None:
    states = []
    for loss in (0.0, 1.0):
        with Simulation(profile_id="democar-network", drop_probability=loss) as sim:
            prepare_drive(sim)
            sim.advance_to(500_000_000)
            states.append(sim.state())
    assert states[0]["gear"] == "D"
    assert states[1]["gear"] == "P"
    assert states[1]["physical_speed_kph"] == 0
