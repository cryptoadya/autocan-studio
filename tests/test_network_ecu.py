from collections.abc import Mapping

import pytest

from autocan.clock import Phase, SimulationClock
from autocan.dbc import load_democar_database
from autocan.events import CanObservation


def observe(name: str, signals: Mapping[str, int | float], time: int = 0) -> CanObservation:
    message = load_democar_database("democar-network").get_message_by_name(name)
    payload = message.encode(signals, strict=True)
    return CanObservation("democar", message.frame_id, False, message.length, payload, time)


def test_kombi_has_independent_timers_and_accepts_delivery_at_deadline() -> None:
    from autocan.network_ecu import NetworkKombi

    clock = SimulationClock()
    kombi = NetworkKombi(clock)
    kombi.receive(observe("EngineStatus", {"EngineRpm": 800}))
    clock.advance_to(60_000_000)
    assert kombi.display_state()["kombi_status"] == "STALE"

    def deliver() -> None:
        kombi.receive(observe("EngineStatus", {"EngineRpm": 1000}, 200_000_000))

    clock.schedule(200_000_000, Phase.DELIVERY, deliver)
    clock.advance_to(200_000_000)
    assert kombi.display_state()["kombi_status"] == "FRESH"
    assert kombi.display_state()["kombi_rpm"] == 1000
    assert kombi.display_state()["kombi_speed_status"] == "TIMEOUT"
    assert kombi.display_state()["kombi_speed_kph"] is None
    clock.advance_to(400_000_000)
    assert kombi.display_state()["kombi_status"] == "TIMEOUT"


def test_bad_wheel_payload_does_not_refresh_kombi_speed() -> None:
    from autocan.network_ecu import NetworkKombi

    clock = SimulationClock()
    kombi = NetworkKombi(clock)
    signals = {name: -10 for name in ("FrontLeft", "FrontRight", "RearLeft", "RearRight")}
    kombi.receive(observe("WheelSpeeds", signals))
    assert kombi.display_state()["kombi_speed_kph"] == -10
    clock.advance_to(60_000_000)
    with pytest.raises(ValueError):
        kombi.receive(CanObservation("democar", 0x210, False, 1, b"\x00", clock.now_ns))
    assert kombi.display_state()["kombi_speed_status"] == "STALE"


class Actuator:
    def __init__(self) -> None:
        self.selected: list[str] = []

    def select_gear(self, gear: str) -> tuple[bool, str | None]:
        self.selected.append(gear)
        return True, None


def test_transmission_uses_only_fresh_can_inputs_for_its_actuator() -> None:
    from autocan.network_ecu import TransmissionNode

    clock = SimulationClock()
    actuator = Actuator()
    transmission = TransmissionNode(clock, actuator)
    transmission.receive(observe("EngineStatus", {"EngineRpm": 800}))
    transmission.receive(
        observe(
            "WheelSpeeds",
            {name: 0 for name in ("FrontLeft", "FrontRight", "RearLeft", "RearRight")},
        )
    )
    transmission.receive(observe("BodyState", {"Ignition": 2, "EngineLink": 0, "ClusterLink": 0}))
    transmission.receive(
        observe(
            "DriverInput", {"Throttle": 0, "Brake": 100, "RequestedGear": 3, "RequestSequence": 1}
        )
    )
    assert actuator.selected == ["D"]
    clock.advance_to(210_000_000)
    transmission.receive(
        observe(
            "DriverInput",
            {"Throttle": 0, "Brake": 100, "RequestedGear": 1, "RequestSequence": 2},
            clock.now_ns,
        )
    )
    assert actuator.selected == ["D"]
    frame = transmission.publish("TransmissionState", frame_id=1, created_at_ns=clock.now_ns)
    decoded = (
        load_democar_database("democar-network")
        .get_message_by_name("TransmissionState")
        .decode_simple(frame.payload, decode_choices=False)
    )
    assert decoded["Gear"] == 3
    assert decoded["ShiftAccepted"] == 0
    assert decoded["ShiftReason"] == 3
