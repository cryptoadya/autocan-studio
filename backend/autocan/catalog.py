"""Packaged, original demo scenarios and DBC-derived fault editor choices."""

from hashlib import sha256
from importlib.resources import files
from pathlib import Path

from autocan.dbc import load_democar_database
from autocan.faults import ANALOG_SIGNALS, FAULT_TYPES, SIGNAL_TYPES
from autocan.network_profile import PLAN_BY_ID
from autocan.scenarios import Scenario
from autocan.validation import json_object

DEMOS = {
    "abs_missing_frames": (
        "ABS missing frames",
        "ABS stops publishing during acceleration; KOMBI becomes STALE, then TIMEOUT.",
    ),
    "rpm_spike": (
        "RPM spike",
        "7000 RPM on CAN while the physical engine continues normally, followed by recovery.",
    ),
    "timing_jitter": (
        "Timing jitter",
        "A slower EngineStatus cadence and seeded receiver delays, then normal timing.",
    ),
}


def load_demo(identifier: str) -> tuple[dict[str, object], Scenario]:
    if identifier not in DEMOS:
        raise ValueError("unknown demo scenario")
    name = f"{identifier}.json"
    packaged = files("autocan").joinpath("demo_scenarios", name)
    # Editable checkouts keep the single source under scenarios/. Wheels include
    # those exact files through Hatch force-include; no duplicate JSON is maintained.
    raw = (
        packaged.read_bytes()
        if packaged.is_file()
        else (Path(__file__).resolve().parents[2] / "scenarios" / name).read_bytes()
    )
    scenario = Scenario.from_dict(json_object(raw.decode()))
    title, description = DEMOS[identifier]
    return {
        "id": identifier,
        "name": title,
        "description": description,
        "duration_ns": scenario.duration_ns,
        "profile_id": scenario.profile_id,
        "seed": scenario.seed,
        "source_sha256": sha256(raw).hexdigest(),
    }, scenario


def fault_catalog() -> dict[str, object]:
    def parameter(
        name: str,
        label: str,
        minimum: float,
        maximum: float,
        step: float,
        default: float | None,
        *,
        scale: int = 1,
        optional: bool = False,
    ) -> dict[str, object]:
        return {
            "name": name,
            "label": label,
            "minimum": minimum,
            "maximum": maximum,
            "step": step,
            "default": default,
            "scale": scale,
            "optional": optional,
        }

    values = [parameter("value", "Value", -10000, 10000, 0.01, 7000)]
    parameters = {
        "ecu_offline": [],
        "message_drop": [parameter("probability", "Drop probability (0–1)", 0, 1, 0.01, 0.5)],
        "message_delay": [
            parameter("min_ns", "Minimum delay (ms)", 0, 1000, 1, 0, scale=1000000),
            parameter("max_ns", "Maximum delay (ms)", 0, 1000, 1, 60, scale=1000000),
        ],
        "signal_spike": values,
        "signal_stuck": [
            parameter(
                "value", "Held value (blank = capture)", -10000, 10000, 0.01, None, optional=True
            )
        ],
        "signal_bias": [
            parameter("offset", "Offset", -10000, 10000, 0.01, 100),
            parameter("rate_per_second", "Bias per second", -10000, 10000, 0.01, 0, optional=True),
        ],
        "message_frequency_anomaly": [
            parameter("period_ns", "Period (ms)", 10, 5000, 10, 60, scale=1000000)
        ],
        "cross_signal_inconsistency": [
            parameter("offset", "Wheel-speed offset (km/h)", -10000, 10000, 0.01, 40)
        ],
    }
    return {
        "types": [
            {
                "id": kind,
                "name": kind.replace("_", " ").capitalize(),
                "requires_signal": kind in SIGNAL_TYPES,
                "wheel_only": kind == "cross_signal_inconsistency",
                "parameters": parameters[kind],
            }
            for kind in FAULT_TYPES
        ],
        "messages": [
            {
                "can_id": message.frame_id,
                "name": message.name,
                "ecu_id": PLAN_BY_ID[message.frame_id].source_ecu,
                "signals": [
                    {
                        "name": signal.name,
                        "minimum": signal.minimum,
                        "maximum": signal.maximum,
                        "step": signal.scale,
                        "unit": signal.unit or "",
                    }
                    for signal in message.signals
                    if signal.name in ANALOG_SIGNALS
                ],
            }
            for message in load_democar_database("democar-network").messages
        ],
    }
