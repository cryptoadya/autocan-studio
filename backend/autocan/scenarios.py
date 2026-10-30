"""Small declarative JSON scenarios; no expressions, plugins, or executable code."""

from dataclasses import dataclass
from pathlib import Path

from autocan.simulation import PHYSICS_TICK_NS, Simulation
from autocan.validation import fields, integer, json_object, number, object_value

MAX_SCENARIO_BYTES = 64 * 1024
MAX_COMMANDS = 256


@dataclass(frozen=True, slots=True)
class ScenarioCommand:
    at_ns: int
    kind: str
    value: float | bool

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ScenarioCommand":
        kind = data.get("kind")
        at = integer(data.get("at_ns"), "at_ns")
        if at % PHYSICS_TICK_NS:
            raise ValueError("command time must align with the physics grid")
        if kind == "set_throttle":
            fields(data, {"at_ns", "kind", "percent"})
            value: float | bool = number(data["percent"], "percent", minimum=0, maximum=100)
        elif kind == "set_engine_publishing":
            fields(data, {"at_ns", "kind", "enabled"})
            enabled = data["enabled"]
            if type(enabled) is not bool:
                raise ValueError("enabled must be a boolean")
            value = enabled
        else:
            raise ValueError("unsupported command kind")
        return cls(at, kind, value)

    def schedule(self, sim: Simulation) -> None:
        # Revalidate even programmatically constructed scenarios.
        if self.kind == "set_throttle" and not isinstance(self.value, bool):
            sim.set_throttle(self.value, at_ns=self.at_ns)
        elif self.kind == "set_engine_publishing" and type(self.value) is bool:
            sim.set_engine_publishing(self.value, at_ns=self.at_ns)
        else:
            raise ValueError("invalid scenario command")


@dataclass(frozen=True, slots=True)
class Scenario:
    duration_ns: int
    commands: tuple[ScenarioCommand, ...] = ()
    seed: int = 0
    delivery_delay_ns: int = 0
    drop_probability: float = 0

    def __post_init__(self) -> None:
        integer(self.duration_ns, "duration_ns")
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
            parameters["percent" if command.kind == "set_throttle" else "enabled"] = command.value
            ScenarioCommand.from_dict(parameters)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Scenario":
        fields(data, {"schema_version", "duration_ns", "commands"}, {"seed", "transport"})
        if integer(data["schema_version"], "schema_version") != 1:
            raise ValueError("unsupported scenario schema")
        commands = data["commands"]
        if not isinstance(commands, list) or len(commands) > MAX_COMMANDS:
            raise ValueError("commands must be a bounded list")
        transport = object_value(data.get("transport", {}), "transport")
        fields(transport, set(), {"delay_ns", "drop_probability"})
        return cls(
            duration_ns=integer(data["duration_ns"], "duration_ns"),
            commands=tuple(ScenarioCommand.from_dict(object_value(c, "command")) for c in commands),
            seed=integer(data.get("seed", 0), "seed"),
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
