"""Executable wire contract for the original DemoCar EngineStatus message."""

from pathlib import Path

import pytest
from cantools.database.can import Message
from cantools.database.errors import DecodeError, EncodeError

from autocan.dbc import load_democar_database


@pytest.fixture
def engine_status() -> Message:
    return load_democar_database().get_message_by_name("EngineStatus")


def test_message_identity_and_layout(engine_status: Message) -> None:
    assert engine_status.frame_id == 0x200
    assert not engine_status.is_extended_frame
    assert not engine_status.is_fd
    assert engine_status.length == 2
    assert engine_status.cycle_time == 20
    assert engine_status.senders == ["Engine"]
    assert len(engine_status.signals) == 1
    signal = engine_status.signals[0]
    assert signal.name == "EngineRpm"
    assert signal.receivers == ["KOMBI"]
    assert (signal.start, signal.length, signal.byte_order) == (0, 16, "little_endian")
    assert not signal.is_signed
    assert (signal.scale, signal.offset, signal.minimum, signal.maximum) == (0.25, 0, 0, 8000)
    assert signal.unit == "rpm"


@pytest.mark.parametrize(
    ("rpm", "payload_hex"),
    [(0, "0000"), (800, "800c"), (1234.25, "4913"), (3000, "e02e"), (8000, "007d")],
)
def test_golden_encode_and_decode(engine_status: Message, rpm: float, payload_hex: str) -> None:
    # Golden bytes are calculated from raw = rpm * 4, unsigned 16-bit little-endian.
    assert engine_status.encode({"EngineRpm": rpm}, strict=True) == bytes.fromhex(payload_hex)
    assert engine_status.decode(bytes.fromhex(payload_hex)) == {"EngineRpm": rpm}


@pytest.mark.parametrize(
    ("rpm", "decoded"),
    [(800.10, 800.0), (800.125, 800.0), (800.375, 800.5), (800.40, 800.5)],
)
def test_quarter_rpm_rounding_uses_nearest_ties_to_even(
    engine_status: Message, rpm: float, decoded: float
) -> None:
    payload = engine_status.encode({"EngineRpm": rpm}, strict=True)
    assert engine_status.decode(payload) == {"EngineRpm": decoded}


@pytest.mark.parametrize("rpm", [-0.01, -1, 8000.01, 16383.75])
def test_rejects_values_outside_declared_range(engine_status: Message, rpm: float) -> None:
    with pytest.raises(EncodeError):
        engine_status.encode({"EngineRpm": rpm}, strict=True)


@pytest.mark.parametrize("rpm", [float("nan"), float("inf"), float("-inf")])
def test_rejects_nonfinite_sensor_values(engine_status: Message, rpm: float) -> None:
    with pytest.raises((EncodeError, ValueError, OverflowError)):
        engine_status.encode({"EngineRpm": rpm}, strict=True)


def test_physical_anomaly_can_still_be_valid_on_the_wire(engine_status: Message) -> None:
    payload = engine_status.encode({"EngineRpm": 7500}, strict=True)
    assert payload.hex() == "3075"
    assert engine_status.decode(payload) == {"EngineRpm": 7500}


@pytest.mark.parametrize("signals", [{}, {"EngineRpm": 800, "Typo": 1}])
def test_rejects_missing_or_unexpected_signals(
    engine_status: Message, signals: dict[str, int]
) -> None:
    with pytest.raises(EncodeError):
        engine_status.encode(signals, strict=True)


def test_rejects_truncated_payload(engine_status: Message) -> None:
    with pytest.raises(DecodeError):
        engine_status.decode(b"\x80", allow_truncated=False)


def test_rejects_excess_payload_when_strictly_decoding(engine_status: Message) -> None:
    with pytest.raises(DecodeError):
        engine_status.decode(b"\x80\x0c\x00", allow_excess=False)


def test_loads_packaged_profile_from_another_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    message = load_democar_database().get_message_by_name("EngineStatus")
    assert message.encode({"EngineRpm": 800}).hex() == "800c"


def test_loading_returns_independent_databases() -> None:
    first = load_democar_database()
    first.get_message_by_name("EngineStatus").frame_id = 0x201
    second = load_democar_database()
    assert second.get_message_by_name("EngineStatus").frame_id == 0x200
