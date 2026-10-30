import pytest
from cantools.database.errors import EncodeError

from autocan.dbc import load_democar_database


def test_network_profile_keeps_legacy_rpm_bytes_and_adds_all_ecu_messages() -> None:
    db = load_democar_database("democar-network")
    assert {m.name for m in db.messages} == {
        "EngineStatus",
        "EngineTelemetry",
        "WheelSpeeds",
        "AbsStatus",
        "TransmissionState",
        "BodyState",
        "DriverInput",
        "ClusterHeartbeat",
    }
    assert db.get_message_by_name("EngineStatus").encode({"EngineRpm": 800}).hex() == "800c"
    assert all(m.length <= 8 and not m.is_extended_frame for m in db.messages)
    assert len(load_democar_database().messages) == 1


@pytest.mark.parametrize(("speed", "payload"), [(10, "e803" * 4), (-10, "18fc" * 4)])
def test_wheel_speed_signed_golden_roundtrip(speed: float, payload: str) -> None:
    message = load_democar_database("democar-network").get_message_by_name("WheelSpeeds")
    signals = {name: speed for name in ("FrontLeft", "FrontRight", "RearLeft", "RearRight")}
    assert message.encode(signals, strict=True).hex() == payload
    assert message.decode(bytes.fromhex(payload), decode_choices=False) == signals
    with pytest.raises(EncodeError):
        message.encode({**signals, "FrontLeft": 301}, strict=True)


def test_network_telemetry_uses_explicit_presence_and_original_units() -> None:
    message = load_democar_database("democar-network").get_message_by_name("EngineTelemetry")
    signals = {
        "Throttle": 50,
        "CoolantTemperature": 20,
        "EngineOn": 1,
        "BodyLink": 0,
        "ClusterLink": 0,
    }
    assert message.encode(signals, strict=True).hex() == "64580201"
    assert message.decode(bytes.fromhex("64580201"), decode_choices=False) == signals
    with pytest.raises(EncodeError):
        message.encode({**signals, "CoolantTemperature": 151}, strict=True)
