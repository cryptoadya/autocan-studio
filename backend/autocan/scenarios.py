"""Small declarative JSON scenarios; no expressions, plugins, or executable code."""

from dataclasses import dataclass
from pathlib import Path

from autocan.dbc import profile_version
from autocan.faults import FaultSpec, fault_id
from autocan.network_profile import ECU_IDS
from autocan.simulation import PHYSICS_TICK_NS, Simulation
from autocan.validation import fields, integer, json_object, number, object_value
from autocan.vehicle import validate_gear, validate_ignition

MAX_SCENARIO_BYTES = 64 * 1024
MAX_COMMANDS = 256


def command_parameter(kind: object) -> str:
    if kind == "inject_fault":
        return "fault_json"
    if kind == "clear_fault":
        return "fault_id"
    if kind in ("set_throttle", "set_brake"):
        return "percent"
    if kind == "set_gear":
        return "gear"
    if kind == "set_ignition":
        return "state"
    if kind in ("set_engine_publishing", "set_engine_running", "set_ecu_publishing"):
        return "enabled"
    raise ValueError("unsupported command kind")


@dataclass(frozen=True, slots=True)
class ScenarioCommand:
    at_ns: int
    kind: str
    value: float | bool | str
    ecu_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ScenarioCommand":
        kind = data.get("kind")
        ecu_id: str | None = None
        at = integer(data.get("at_ns"), "at_ns")
        if at % PHYSICS_TICK_NS:
            raise ValueError("command time must align with the physics grid")
        if kind == "inject_fault":
            fields(data, {"at_ns", "kind", "fault"})
            value: float | bool | str = FaultSpec.from_dict(
                object_value(data["fault"], "fault")
            ).json()
        elif kind == "clear_fault":
            fields(data, {"at_ns", "kind", "fault_id"})
            value = fault_id(data["fault_id"])
        elif kind in ("set_throttle", "set_brake"):
            fields(data, {"at_ns", "kind", "percent"})
            value = number(data["percent"], "percent", minimum=0, maximum=100)
        elif kind in ("set_engine_publishing", "set_engine_running"):
            fields(data, {"at_ns", "kind", "enabled"})
            enabled = data["enabled"]
            if type(enabled) is not bool:
                raise ValueError("enabled must be a boolean")
            value = enabled
        elif kind == "set_ignition":
            fields(data, {"at_ns", "kind", "state"})
            state = data["state"]
            if not isinstance(state, str):
                raise ValueError("ignition must be a string")
            validate_ignition(state)
            value = state
        elif kind == "set_gear":
            fields(data, {"at_ns", "kind", "gear"})
            gear = data["gear"]
            if not isinstance(gear, str):
                raise ValueError("gear must be a string")
            validate_gear(gear)
            value = gear
        elif kind == "set_ecu_publishing":
            fields(data, {"at_ns", "kind", "ecu_id", "enabled"})
            target = data["ecu_id"]
            if (
                not isinstance(target, str)
                or target not in ECU_IDS
                or type(data["enabled"]) is not bool
            ):
                raise ValueError("known ECU ID and boolean enabled are required")
            ecu_id, value = target, data["enabled"]
        else:
            raise ValueError("unsupported command kind")
        return cls(at, kind, value, ecu_id)

    @classmethod
    def from_record(cls, record: dict[str, object]) -> "ScenarioCommand":
        kind = record["command_kind"]
        parameter = command_parameter(kind)
        data = {"at_ns": record["applied_at_ns"], "kind": kind, parameter: record[parameter]}
        if kind == "inject_fault":
            raw = data.pop("fault_json")
            if not isinstance(raw, str):
                raise ValueError("fault_json must be a string")
            data["fault"] = json_object(raw)
        if kind == "set_ecu_publishing":
            data["ecu_id"] = record["ecu_id"]
        return cls.from_dict(data)

    def schedule(self, sim: Simulation) -> None:
        # Revalidate even programmatically constructed scenarios.
        if self.kind == "inject_fault" and isinstance(self.value, str):
            sim.inject_fault(FaultSpec.from_dict(json_object(self.value)), at_ns=self.at_ns)
        elif self.kind == "clear_fault" and isinstance(self.value, str):
            sim.clear_fault(self.value, at_ns=self.at_ns)
        elif self.kind == "set_throttle" and not isinstance(self.value, (bool, str)):
            sim.set_throttle(self.value, at_ns=self.at_ns)
        elif self.kind == "set_brake" and not isinstance(self.value, (bool, str)):
            sim.set_brake(self.value, at_ns=self.at_ns)
        elif self.kind == "set_gear" and isinstance(self.value, str):
            sim.set_gear(self.value, at_ns=self.at_ns)
        elif self.kind == "set_engine_publishing" and type(self.value) is bool:
            sim.set_engine_publishing(self.value, at_ns=self.at_ns)
        elif (
            self.kind == "set_ecu_publishing"
            and type(self.value) is bool
            and self.ecu_id is not None
        ):
            sim.set_ecu_publishing(self.ecu_id, self.value, at_ns=self.at_ns)
        elif self.kind == "set_engine_running" and type(self.value) is bool:
            sim.set_engine_running(self.value, at_ns=self.at_ns)
        elif self.kind == "set_ignition" and isinstance(self.value, str):
            sim.set_ignition(self.value, at_ns=self.at_ns)
        else:
            raise ValueError("invalid scenario command")


@dataclass(frozen=True, slots=True)
class Scenario:
    duration_ns: int
    commands: tuple[ScenarioCommand, ...] = ()
    seed: int = 0
    delivery_delay_ns: int = 0
    drop_probability: float = 0
    profile_id: str = "democar"

    def __post_init__(self) -> None:
        integer(self.duration_ns, "duration_ns")
        profile_version(self.profile_id)
        integer(self.seed, "seed")
        integer(self.delivery_delay_ns, "delivery delay")
        number(self.drop_probability, "drop probability", minimum=0, maximum=1)
        if len(self.commands) > MAX_COMMANDS:
            raise ValueError("too many scenario commands")
        for command in self.commands:
            integer(command.at_ns, "command time")
            if command.at_ns > self.duration_ns or command.at_ns % PHYSICS_TICK_NS:
                raise ValueError("command is outside the run or physics grid")
            parameters: dict[str, object] = {"at_ns": command.at_ns, "kind": command.kind}
            parameters[command_parameter(command.kind)] = command.value
            if command.kind in ("inject_fault", "clear_fault"):
                if self.profile_id != "democar-network":
                    raise ValueError("fault library requires the network profile")
                if command.kind == "inject_fault":
                    if not isinstance(command.value, str):
                        raise ValueError("invalid fault command")
                    parameters.pop("fault_json")
                    parameters["fault"] = json_object(command.value)
            if command.kind == "set_ecu_publishing":
                parameters["ecu_id"] = command.ecu_id
                if self.profile_id != "democar-network" and command.ecu_id != "engine":
                    raise ValueError("additional ECU require the network profile")
            ScenarioCommand.from_dict(parameters)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Scenario":
        fields(
            data, {"schema_version", "duration_ns", "commands"}, {"seed", "transport", "profile_id"}
        )
        if integer(data["schema_version"], "schema_version") != 1:
            raise ValueError("unsupported scenario schema")
        commands = data["commands"]
        if not isinstance(commands, list) or len(commands) > MAX_COMMANDS:
            raise ValueError("commands must be a bounded list")
        transport = object_value(data.get("transport", {}), "transport")
        profile = data.get("profile_id", "democar")
        if not isinstance(profile, str):
            raise ValueError("profile_id must be a string")
        fields(transport, set(), {"delay_ns", "drop_probability"})
        return cls(
            duration_ns=integer(data["duration_ns"], "duration_ns"),
            commands=tuple(ScenarioCommand.from_dict(object_value(c, "command")) for c in commands),
            seed=integer(data.get("seed", 0), "seed"),
            profile_id=profile,
            delivery_delay_ns=integer(transport.get("delay_ns", 0), "delay_ns"),
            drop_probability=number(
                transport.get("drop_probability", 0), "drop probability", minimum=0, maximum=1
            ),
        )

    @classmethod
    def load(cls, path: Path) -> "Scenario":
        with path.open("rb") as stream:
            raw = stream.read(MAX_SCENARIO_BYTES + 1)
        if len(raw) > MAX_SCENARIO_BYTES:
            raise ValueError("scenario exceeds size limit")
        return cls.from_dict(json_object(raw.decode("utf-8")))
