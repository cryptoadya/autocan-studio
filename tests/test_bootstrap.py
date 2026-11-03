"""Run-cluster resampling preserves pooled denominators and undefined metrics."""

from dataclasses import replace

import numpy as np
import pytest

from autocan.evaluation import FaultEpisode, summarize
from autocan.saved_evaluation import ScoredRun
from autocan.score_events import evaluate_score_events
from autocan.window_evaluation import (
    Comparison,
    WindowKey,
    WindowScore,
    WindowTruth,
    evaluate_windows,
)


def sample(
    run_id: str, values: list[float], labels: list[bool], *, normal: bool = False
) -> ScoredRun:

    keys = [WindowKey(i, run_id, "test", i * 100, (i + 1) * 100) for i in range(len(values))]
    faults = [] if normal else [FaultEpisode("fault", "spike", 0, len(values) * 100, ())]
    events = evaluate_score_events(
        keys, [v >= 0.5 for v in values], faults, model_id="rule", window_ns=100
    )
    return ScoredRun(
        run_id,
        "test",
        None if normal else "spike",
        keys,
        [*labels],
        {"rule": values},
        {"rule": events},
    )


def test_weighted_metrics_equal_explicit_whole_run_copies_with_ties() -> None:
    from autocan.bootstrap import WindowPool, event_values

    runs = [sample("short", [0.8], [True]), sample("long", [0.8, 0.2, 0.6], [False, True, False])]
    pool = WindowPool(runs, "rule", 0.5, "ge")
    for weights in ([2, 0], [1, 1], [0, 2]):
        truths: list[WindowTruth] = []
        scores, results = [], []
        for i, weight in enumerate(weights):
            for copy in range(weight):
                run = runs[i]
                results.append(run.events["rule"].result)
                for key, value, label in zip(run.keys, run.scores["rule"], run.labels, strict=True):
                    key = replace(key, row_index=len(truths), run_id=f"{i}-{copy}")
                    truths.append(WindowTruth(key, label))
                    scores.append(WindowScore(key, value))
        expected = evaluate_windows(truths, scores, model_id="rule", split="test", threshold=0.5)
        actual = pool.values(np.array(weights))
        for metric in ("precision", "recall", "f1", "average_precision"):
            assert actual[metric] == pytest.approx(expected[metric])
        assert [actual[k] for k in ("tn", "fp", "fn", "tp")] == [
            n for row in expected["confusion_matrix"]["counts"] for n in row
        ]
        events = event_values(runs, "rule", weights)
        summary = summarize(results)
        assert events["detection_rate"] == summary["detection_rate"]
        assert events["latency_p95_ns"] == summary["detection_latency_ns"]["p95"]


def test_bootstrap_is_paired_repeatable_and_does_not_redraw_undefined_replicates() -> None:
    from autocan.bootstrap import bootstrap_runs

    runs = [sample("fault", [0.9], [True]), sample("normal", [0.1] * 9, [False] * 9, normal=True)]
    for run in runs:
        run.scores["identical"] = run.scores["rule"]
        run.events["identical"] = run.events["rule"]
    policies: dict[str, tuple[float, Comparison]] = {"rule": (0.5, "ge"), "identical": (0.5, "ge")}
    first = bootstrap_runs(runs, policies, replicates=100, seed=42)
    assert first == bootstrap_runs(list(reversed(runs)), policies, replicates=100, seed=42)
    rows = first["rows"]
    assert rows[0]["window"] == rows[1]["window"]
    assert rows[0]["event"] == rows[1]["event"]
    recall = rows[0]["window"]["recall"]
    assert 0 < recall["valid_replicates"] < 100
    assert recall["undefined_replicates"] == 100 - recall["valid_replicates"]
    assert recall["lower"] == recall["upper"] == 1
    assert rows[0]["window"]["tn"]["lower"] == 0
    assert rows[0]["window"]["tn"]["upper"] == 18
    assert first["resampling"][0]["runs"] == ["fault", "normal"]


def test_single_run_and_zero_denominators_are_explicit() -> None:
    from autocan.bootstrap import bootstrap_runs

    result = bootstrap_runs(
        [sample("only", [0.1], [False], normal=True)], {"rule": (0.5, "ge")}, replicates=10, seed=1
    )
    row = result["rows"][0]
    assert row["window"]["tn"]["estimate"] == 1
    assert row["window"]["tn"]["lower"] is None
    assert row["window"]["tn"]["status"] == "insufficient_runs"
    assert row["window"]["average_precision"]["undefined_replicates"] == 10
    assert row["event"]["detection_rate"]["estimate"] is None
    assert row["event"]["false_alarms_per_hour"]["estimate"] == 0


@pytest.mark.parametrize("replicates,seed", [(0, 1), (10001, 1), (10, -1), (True, 1)])
def test_invalid_sampling_configuration_fails(replicates: int, seed: int) -> None:
    from autocan.bootstrap import bootstrap_runs

    with pytest.raises(ValueError):
        bootstrap_runs([], {"rule": (0.5, "ge")}, replicates=replicates, seed=seed)
