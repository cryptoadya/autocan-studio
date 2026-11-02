"""Hand-counted temporal event matching on saved, eligible window decisions."""

from dataclasses import replace
from pathlib import Path

import pytest

from autocan.evaluation import FaultEpisode, summarize
from autocan.window_evaluation import Comparison, WindowKey


@pytest.fixture
def saved_scores(
    tmp_path: Path,
) -> tuple[Path, dict[str, object], dict[str, tuple[float, Comparison]]]:
    from autocan.ml_experiment import SCORE_SCHEMA
    from autocan.parquet import _finish, _TableWriter

    policies: dict[str, tuple[float, Comparison]] = {
        "heartbeat_timeout": (0.5, "ge"),
        "rpm_derivative": (0.5, "gt"),
        "wheel_residual": (0.5, "gt"),
        "isolation_forest": (0.5, "ge"),
        "random_forest": (0.5, "ge"),
    }
    writer = _TableWriter(tmp_path, "scores.parquet", SCORE_SCHEMA, 65536)
    for key in keys(3):
        for name, (threshold, comparison) in policies.items():
            writer.write(
                {
                    "row_index": key.row_index,
                    "model_id": name,
                    "score": 0.5,
                    "threshold": threshold,
                    "comparison": comparison,
                    "anomalous": comparison == "ge",
                }
            )
    writer.close()
    manifest = _finish(tmp_path, {}, {"scores.parquet": writer}, 65536)
    return tmp_path, manifest, policies


def test_saved_scores_preserve_exact_threshold_boundaries_and_all_window_keys(
    saved_scores: tuple[Path, dict[str, object], dict[str, tuple[float, Comparison]]],
) -> None:
    from autocan.score_artifacts import iter_saved_decisions

    root, manifest, policies = saved_scores
    actual = list(iter_saved_decisions(root, manifest, keys(3), policies))
    assert (
        actual
        == [
            {
                "heartbeat_timeout": True,
                "rpm_derivative": False,
                "wheel_residual": False,
                "isolation_forest": True,
                "random_forest": True,
            }
        ]
        * 3
    )


@pytest.mark.parametrize(
    "damage",
    [
        "key",
        "model",
        "threshold",
        "decision",
        "missing",
        "extra",
        "schema",
        "semantic",
        "nonfinite",
    ],
)
def test_rehashed_score_tables_cannot_change_alignment_cutoffs_or_decisions(
    saved_scores: tuple[Path, dict[str, object], dict[str, tuple[float, Comparison]]],
    damage: str,
) -> None:
    from hashlib import sha256

    import pyarrow as pa
    import pyarrow.parquet as pq

    from autocan.score_artifacts import iter_saved_decisions
    from autocan.validation import canonical_bytes

    root, manifest, policies = saved_scores
    path = root / "scores.parquet"
    table = pq.read_table(path)
    rows = table.to_pylist()
    if damage == "key":
        rows[0]["row_index"] = 1
    elif damage == "model":
        rows[0]["model_id"] = "random_forest"
    elif damage == "threshold":
        rows[0]["threshold"] = 0.4
    elif damage == "decision":
        rows[0]["anomalous"] = False
    elif damage == "missing":
        rows.pop()
    elif damage == "extra":
        rows.append(rows[-1])
    elif damage == "nonfinite":
        rows[0]["score"] = float("nan")
    table = pa.Table.from_pylist(rows, schema=table.schema)
    if damage == "schema":
        table = table.select(list(reversed(table.column_names)))
    pq.write_table(table, path, row_group_size=256)
    from autocan.validation import object_value

    info = object_value(
        object_value(manifest["artifacts"], "artifacts")["scores.parquet"], "scores"
    )
    info.update(
        bytes=path.stat().st_size,
        sha256=sha256(path.read_bytes()).hexdigest(),
        records=len(rows),
        semantic_sha256=(
            "0" * 64
            if damage in ("semantic", "nonfinite")
            else sha256(b"".join(canonical_bytes(r) + b"\n" for r in rows)).hexdigest()
        ),
    )
    with pytest.raises(ValueError):
        list(iter_saved_decisions(root, manifest, keys(3), policies))


def keys(count: int = 12) -> list[WindowKey]:
    return [WindowKey(i, "r", "test", i * 100, (i + 1) * 100, i >= 2) for i in range(count)]


def test_onsets_grace_preexisting_alarms_and_original_latency_with_censoring() -> None:
    from autocan.score_events import evaluate_score_events

    faults = [
        FaultEpisode("warm", "delay", 10, 150, (1,)),
        FaultEpisode("left", "delay", 50, 350, (528,)),
        FaultEpisode("preexisting", "offline", 350, 450, (512,)),
        FaultEpisode("a", "spike", 550, 570, (512,)),
        FaultEpisode("b", "spike", 750, 800, (528,)),
        FaultEpisode("upper", "stuck", 850, 900, (512,)),
        FaultEpisode("right", "delay", 1150, 1500, (512,)),
        FaultEpisode("tail", "stuck", 1250, 1300, (512,)),
    ]
    run = evaluate_score_events(
        keys(),
        [True, True, True, True, False, True, False, True, False, True, False, True],
        faults,
        model_id="random_forest",
        window_ns=100,
    )
    assert [(a.start_ns, a.end_ns, a.can_ids) for a in run.result.alerts] == [
        (300, 500, ()),
        (600, 700, ()),
        (800, 900, ()),
        (1000, 1100, ()),
        (1200, None, ()),
    ]
    assert [
        (run.result.faults[m.fault_index].fault_id, m.latency_ns) for m in run.result.matches
    ] == [("left", 250), ("a", 50), ("b", 50), ("right", 50)]
    assert [f.fault_id for f in run.unscored_faults] == ["warm", "tail"]
    assert run.left_censored_fault_ids == ("left",)
    assert run.right_censored_fault_ids == ("right",)
    assert run.result.faults[0].start_ns == 50  # latency never restarts at warm-up boundary
    assert run.result.faults[-1].end_ns == 1200
    metrics = summarize([run.result])
    assert metrics["fault_episodes"] == 6 and metrics["detected_episodes"] == 4
    assert metrics["detection_rate"] == pytest.approx(2 / 3)
    assert metrics["detection_latency_ns"] == {"mean": 100, "p50": 50, "p95": 250}
    assert metrics["by_fault_type"]["offline"]["detection_rate"] == 0
    assert metrics["by_fault_type"]["stuck"]["detection_latency_ns"]["p95"] is None
    assert metrics["false_alarms_per_hour"] is None


def test_overlapping_faults_get_one_credit_per_onset_and_earliest_expiry_wins() -> None:
    from autocan.score_events import evaluate_score_events

    faults = [
        FaultEpisode("long", "bias", 250, 800, (512,)),
        FaultEpisode("short", "delay", 250, 310, (528,)),
    ]
    run = evaluate_score_events(
        keys(8),
        [False, False, True, True, False, True, False, False],
        faults,
        model_id="heartbeat_timeout",
        window_ns=100,
    )
    assert [(m.fault_index, m.alert_index, m.latency_ns) for m in run.result.matches] == [
        (1, 0, 50),
        (0, 1, 350),
    ]


def test_fault_at_final_decision_time_is_censored_and_tail_only_fault_is_unscored() -> None:
    from autocan.score_events import evaluate_score_events

    result = evaluate_score_events(
        keys(4),
        [False, False, False, True],
        [
            FaultEpisode("edge", "spike", 400, 450, (1,)),
            FaultEpisode("tail", "spike", 401, 450, (1,)),
        ],
        model_id="random_forest",
        window_ns=100,
    )
    assert len(result.result.faults) == len(result.result.matches) == 1
    assert result.result.matches[0].latency_ns == 0
    assert result.right_censored_fault_ids == ("edge",)
    assert result.unscored_faults[0].fault_id == "tail"


def test_normal_exposure_warmup_empty_span_and_run_identity_are_explicit() -> None:
    from autocan.score_events import evaluate_score_events

    normal = evaluate_score_events(
        keys(6),
        [True, True, True, False, True, True],
        [],
        model_id="isolation_forest",
        window_ns=100,
    )
    assert normal.result.normal_duration_ns == 400
    assert len(normal.result.alerts) == 2
    assert summarize([normal.result])["false_alarms_per_hour"] == 18_000_000_000
    no_evidence = evaluate_score_events(
        [replace(k, eligible=False) for k in keys(2)],
        [True, True],
        [FaultEpisode("a", "drop", 10, 100, (1,))],
        model_id="isolation_forest",
        window_ns=100,
    )
    assert not no_evidence.result.faults and not no_evidence.result.alerts
    assert len(no_evidence.unscored_faults) == 1
    assert summarize([no_evidence.result])["detection_rate"] is None
    with pytest.raises(ValueError, match="alignment|identity"):
        evaluate_score_events(
            [keys()[0], replace(keys()[1], run_id="other")],
            [False, True],
            [],
            model_id="random_forest",
            window_ns=100,
        )
    with pytest.raises(ValueError):
        evaluate_score_events(keys(), [True], [], model_id="random_forest", window_ns=100)
