"""Training statistics must be invariant to held-out values and labels."""

import pytest


def test_only_eligible_training_rows_fit_imputer_and_population_scaler() -> None:
    from autocan.preprocessing import FitSample, fit_training

    rows = [
        FitSample(0, "train", True, True, (1.0, None, 7.0)),
        FitSample(1, "train", True, False, (3.0, 10.0, 7.0)),
        FitSample(2, "train", True, True, (None, 20.0, 7.0)),
        FitSample(3, "train", False, True, (9999.0, 9999.0, 9999.0)),
        FitSample(4, "validation", True, True, (1e9, -1e9, 1e9)),
        FitSample(5, "test", True, True, (-1e9, 1e9, 1e9)),
    ]
    state = fit_training(iter(rows), ("a", "b", "c"), normal_only=False)
    assert state.fit_rows == 3
    assert state.medians == (2, 15, 7)
    assert state.means == (2, 15, 7)
    assert state.scales[0] == pytest.approx((2 / 3) ** 0.5)
    assert state.scales[1] == pytest.approx((50 / 3) ** 0.5)
    assert state.scales[2] == 1
    assert state.transform((None, 15.0, 7.0), columns=("a", "b", "c")) == (0, 0, 0)
    assert fit_training(iter(rows[:4]), ("a", "b", "c"), normal_only=False) == state
    normal = fit_training(iter(rows), ("a", "b", "c"), normal_only=True)
    assert normal.fit_rows == 2 and normal.medians == (1, 20, 7)
    assert normal.means == (1, 20, 7)


def test_all_missing_columns_and_safe_json_roundtrip() -> None:
    from autocan.preprocessing import FitSample, Preprocessor, fit_training

    state = fit_training(
        iter([FitSample(7, "train", True, True, (None, 4.0))]),
        ("missing", "constant"),
        normal_only=True,
    )
    assert state.medians == (0, 4) and state.scales == (1, 1)
    assert state.observed == (0, 1)
    loaded = Preprocessor.from_dict(state.as_dict())
    assert loaded == state
    assert loaded.transform((None, 5.0), columns=state.columns) == (0, 1)
    with pytest.raises(ValueError, match="columns"):
        loaded.transform((1, 2), columns=("constant", "missing"))
    bad = state.as_dict()
    bad["scales"] = [0, 1]
    with pytest.raises(ValueError):
        Preprocessor.from_dict(bad)
    with pytest.raises(ValueError, match="training"):
        fit_training(iter([FitSample(0, "test", True, True, (1.0,))]), ("a",), normal_only=False)


def test_fit_quota_is_explicit_and_excludes_held_out_rows() -> None:
    from autocan.preprocessing import FitSample, fit_training

    samples = [FitSample(i, "train", True, True, (float(i),)) for i in range(3)]
    with pytest.raises(ValueError, match="quota"):
        fit_training(iter(samples), ("a",), normal_only=False, max_values=2)
