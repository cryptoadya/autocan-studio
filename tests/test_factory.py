"""Generated modes and series must demonstrate motion and reproduce real traces."""

import json
import subprocess
import sys
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from autocan.scenarios import Scenario
from autocan.simulation import Simulation
from autocan.traces import TraceError, playback, rerun


@pytest.mark.parametrize(
    "mode", ["idle", "stop_and_go", "city", "cruise", "braking", "acceleration"]
)
@pytest.mark.parametrize("duration", [8_000_000_000, 30_000_000_000])
def test_generated_modes_have_distinct_physical_behaviors(mode: str, duration: int) -> None:
    from autocan.factory import FactoryConfig, generate_recipes

    recipe = next(
        generate_recipes(
            FactoryConfig(
                runs=1,
                modes=(mode,),
                fault_runs=0,
                seed=42,
                min_duration_ns=duration,
                max_duration_ns=duration,
            )
        )
    )
    scenario = Scenario.from_dict(recipe["scenario"])
    speeds: list[float] = []
    with Simulation(seed=scenario.seed, profile_id=scenario.profile_id) as sim:
        for command in scenario.commands:
            command.schedule(sim)
        for now in range(0, scenario.duration_ns + 1, 100_000_000):
            sim.advance_to(now)
            speeds.append(float(str(sim.state()["physical_speed_kph"])))
        assert sim.state()["shift_rejection"] is None
    if mode == "idle":
        assert max(speeds) == 0
    else:
        assert max(speeds) > 3
    if mode in {"braking", "city", "stop_and_go"}:
        assert speeds[-1] == 0
    if mode == "acceleration":
        assert speeds[-1] == max(speeds)
        assert all(after >= before for before, after in zip(speeds, speeds[1:], strict=False))
    if mode == "cruise":
        assert max(speeds[len(speeds) // 2 :]) - min(speeds[len(speeds) // 2 :]) < 0.01
    if mode in {"city", "stop_and_go"}:
        starts = sum(before == 0 < after for before, after in zip(speeds, speeds[1:], strict=False))
        assert starts >= (3 if mode == "stop_and_go" else 2)


def test_recipes_balance_modes_vary_inputs_and_keep_fault_rng_independent() -> None:
    from autocan.factory import FactoryConfig, generate_recipes

    clean = list(generate_recipes(FactoryConfig(runs=24, seed=71, fault_runs=0)))
    faulty = list(generate_recipes(FactoryConfig(runs=24, seed=71, fault_runs=6)))
    assert clean == list(generate_recipes(FactoryConfig(runs=24, seed=71, fault_runs=0)))
    assert clean != list(generate_recipes(FactoryConfig(runs=24, seed=72, fault_runs=0)))
    assert len({row["seed"] for row in clean}) == 24
    assert len({row["scenario"]["duration_ns"] for row in clean}) > 6
    for mode in {row["mode"] for row in clean}:
        recipes = [row for row in clean if row["mode"] == mode]
        assert len(recipes) == 4
        if mode != "idle":
            throttles = {
                tuple(
                    c.value
                    for c in Scenario.from_dict(r["scenario"]).commands
                    if c.kind == "set_throttle"
                )
                for r in recipes
            }
            assert len(throttles) > 1
    episodes = 0
    starts: set[int] = set()
    for normal, fault in zip(clean, faulty, strict=True):
        commands = Scenario.from_dict(fault["scenario"]).commands
        assert (
            tuple(c for c in commands if c.kind not in {"inject_fault", "clear_fault"})
            == Scenario.from_dict(normal["scenario"]).commands
        )
        assert normal["seed"] == fault["seed"]
        injected = [c for c in commands if c.kind == "inject_fault"]
        episodes += len(injected)
        starts.update(c.at_ns for c in injected)
        assert len(injected) == sum(c.kind == "clear_fault" for c in commands)
    assert episodes == 6
    assert len(starts) > 1


def test_series_roundtrips_and_has_reproducible_logical_hash(tmp_path: Path) -> None:
    from autocan.factory import FactoryConfig, generate_factory, verify_factory
    from autocan.parquet import import_trace

    config = FactoryConfig(runs=2, modes=("city", "cruise"), fault_runs=1, seed=17)
    first = generate_factory(config, tmp_path / "first")
    second = generate_factory(config, tmp_path / "second")
    assert first["semantic_sha256"] == second["semantic_sha256"]
    assert first["runs"][0]["run_id"] != second["runs"][0]["run_id"]
    assert verify_factory(tmp_path / "first")["semantic_sha256"] == first["semantic_sha256"]
    assert len(first["runs"]) == 2
    for item in first["runs"]:
        run = tmp_path / "first" / item["path"]
        assert rerun(run / "trace")["matches"] is True
        restored = tmp_path / f"restored-{item['index']}"
        import_trace(run / "parquet", restored)
        assert list(playback(restored)) == list(playback(run / "trace"))
        rows = pq.read_table(run / "observer/observations.parquet")
        assert set(rows.column_names) == {
            "received_at_ns",
            "channel",
            "can_id",
            "is_extended_id",
            "dlc",
            "payload",
        }
        labels = pq.read_table(run / "observer/labels/faults.parquet")
        assert labels.num_rows == (2 if item["fault_type"] else 0)
        assert Scenario.load(run / "scenario.json").seed == item["seed"]


def test_series_quota_overwrite_and_corruption_are_explicit(tmp_path: Path) -> None:
    from autocan.factory import FactoryConfig, generate_factory, verify_factory

    target = tmp_path / "series"
    with pytest.raises(TraceError, match="quota"):
        generate_factory(FactoryConfig(runs=2, max_bytes=4096), target)
    assert not target.exists()
    assert not list(tmp_path.glob(".autocan-*"))
    meta = generate_factory(FactoryConfig(runs=1, modes=("idle",), fault_runs=0), target)
    original = (target / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        generate_factory(FactoryConfig(runs=1), target)
    assert (target / "manifest.json").read_bytes() == original
    path = target / meta["runs"][0]["path"] / "observer/observations.parquet"
    with path.open("ab") as stream:
        stream.write(b"corrupt")
    with pytest.raises(TraceError, match="checksum|size"):
        verify_factory(target)


@pytest.mark.parametrize(
    "options",
    [
        {"runs": 0},
        {"runs": 301},
        {"runs": True},
        {"seed": -1},
        {"fault_runs": 25},
        {"modes": ()},
        {"modes": ("unknown",)},
        {"modes": ("idle", "idle")},
        {"receiver_id": "invalid"},
        {"min_duration_ns": 7_000_000_000},
        {"max_duration_ns": 31_000_000_000},
        {"min_duration_ns": 12_000_000_001},
        {"min_duration_ns": 14_000_000_000},
        {"fault_types": ()},
        {"fault_types": ("unknown",)},
        {"max_bytes": 4095},
    ],
)
def test_invalid_factory_config_fails_before_any_generation(options: dict[str, object]) -> None:
    from autocan.factory import FactoryConfig

    with pytest.raises(ValueError):
        FactoryConfig.from_dict(options)


def test_all_library_fault_types_can_be_generated_and_rerun(tmp_path: Path) -> None:
    from autocan.factory import FactoryConfig, generate_recipes
    from autocan.faults import FAULT_TYPES
    from autocan.traces import record_scenario

    config = FactoryConfig(runs=8, modes=("acceleration",), fault_runs=8, fault_types=FAULT_TYPES)
    recipes = list(generate_recipes(config))
    assert {r["fault_type"] for r in recipes} == set(FAULT_TYPES)
    for index, recipe in enumerate(recipes):
        source = tmp_path / str(index)
        record_scenario(Scenario.from_dict(recipe["scenario"]), source)
        assert rerun(source)["matches"] is True
        events = list(playback(source))
        assert sum(e["event_kind"] == "FaultActivated" for e in events) == 1
        assert sum(e["event_kind"] == "FaultCleared" for e in events) == 1


def test_cli_generates_and_verifies_a_series(tmp_path: Path) -> None:
    target = tmp_path / "factory"
    for args in (
        [
            "generate",
            "--output",
            str(target),
            "--runs",
            "1",
            "--modes",
            "idle",
            "--fault-runs",
            "0",
        ],
        ["verify-dataset", str(target)],
    ):
        result = subprocess.run(
            [sys.executable, "-m", "autocan", *args], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["status"] == "complete"


def test_verifier_rejects_changed_recipes_paths_and_symlink_directories(tmp_path: Path) -> None:
    from autocan.factory import FactoryConfig, generate_factory, verify_factory

    target = tmp_path / "series"
    generate_factory(FactoryConfig(runs=1, modes=("idle",), fault_runs=0), target)
    manifest_path = target / "manifest.json"
    original = manifest_path.read_bytes()
    for field, value in (("path", "../../outside"), ("mode", "cruise"), ("index", False)):
        meta = json.loads(original)
        meta["runs"][0][field] = value
        manifest_path.write_text(json.dumps(meta))
        with pytest.raises(TraceError):
            verify_factory(target)
    meta = json.loads(original)
    key = next(iter(meta["artifacts"]))
    meta["artifacts"]["../outside"] = meta["artifacts"].pop(key)
    manifest_path.write_text(json.dumps(meta))
    with pytest.raises(TraceError, match="paths"):
        verify_factory(target)
    manifest_path.write_bytes(original)
    observer = target / "runs/0000/observer"
    outside = tmp_path / "outside"
    observer.rename(outside)
    observer.symlink_to(outside, target_is_directory=True)
    with pytest.raises(TraceError, match="symlink"):
        verify_factory(target)
