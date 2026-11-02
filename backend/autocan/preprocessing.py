"""Safe JSON median imputation and population scaling, fitted only on train."""

import math
from array import array
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from hashlib import sha256
from statistics import median

from autocan.validation import canonical_bytes, fields, integer, number, object_value

PREPROCESSING_VERSION = "train-median-standard-1"
MAX_FEATURES = 128
MAX_FIT_VALUES = 8_000_000


@dataclass(frozen=True, slots=True)
class FitSample:
    row_index: int
    split: str
    eligible: bool
    normal_run: bool
    values: tuple[float | None, ...]


@dataclass(frozen=True, slots=True)
class Preprocessor:
    columns: tuple[str, ...]
    medians: tuple[float, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    observed: tuple[int, ...]
    fit_rows: int
    normal_only: bool
    fit_rows_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "format": "autocan-preprocessing",
            "format_version": 1,
            "algorithm": PREPROCESSING_VERSION,
            **asdict(self),
        }

    @classmethod
    def from_dict(cls, value: object) -> "Preprocessor":
        data = object_value(value, "preprocessor")
        fields(
            data,
            {
                "format",
                "format_version",
                "algorithm",
                "columns",
                "medians",
                "means",
                "scales",
                "observed",
                "fit_rows",
                "normal_only",
                "fit_rows_sha256",
            },
        )
        if (
            data["format"] != "autocan-preprocessing"
            or data["format_version"] != 1
            or data["algorithm"] != PREPROCESSING_VERSION
        ):
            raise ValueError("unsupported preprocessor")
        columns = data["columns"]
        if (
            not isinstance(columns, (list, tuple))
            or not 0 < len(columns) <= MAX_FEATURES
            or any(not isinstance(v, str) or not v for v in columns)
            or len(set(columns)) != len(columns)
        ):
            raise ValueError("invalid feature columns")
        vectors: list[tuple[float, ...]] = []
        for name in ("medians", "means", "scales"):
            values = data[name]
            if not isinstance(values, (list, tuple)) or len(values) != len(columns):
                raise ValueError("preprocessor vector shape mismatch")
            vectors.append(tuple(number(v, name, minimum=-1e100, maximum=1e100) for v in values))
        if any(value <= 0 for value in vectors[2]):
            raise ValueError("scales must be positive")
        count = integer(data["fit_rows"], "fit rows", minimum=1)
        observed = data["observed"]
        if not isinstance(observed, (list, tuple)) or len(observed) != len(columns):
            raise ValueError("observation vector shape mismatch")
        counts = tuple(integer(value, "observed") for value in observed)
        if any(value > count for value in counts) or type(data["normal_only"]) is not bool:
            raise ValueError("invalid training scope or counts")
        digest = data["fit_rows_sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("invalid fit rows checksum")
        return cls(
            tuple(columns),
            vectors[0],
            vectors[1],
            vectors[2],
            counts,
            count,
            data["normal_only"],
            digest,
        )

    def transform(
        self, values: tuple[float | None, ...], *, columns: tuple[str, ...]
    ) -> tuple[float, ...]:
        if columns != self.columns or len(values) != len(self.columns):
            raise ValueError("feature columns/order mismatch")
        result = []
        for value, replacement, mean, scale in zip(
            values, self.medians, self.means, self.scales, strict=True
        ):
            actual = (
                replacement
                if value is None
                else number(value, "feature", minimum=-1e100, maximum=1e100)
            )
            normalized = (actual - mean) / scale
            if not math.isfinite(normalized):
                raise ValueError("nonfinite transformed feature")
            result.append(normalized)
        return tuple(result)


def fit_training(
    samples: Iterable[FitSample],
    columns: tuple[str, ...],
    *,
    normal_only: bool,
    max_values: int = MAX_FIT_VALUES,
) -> Preprocessor:
    if not 0 < len(columns) <= MAX_FEATURES or len(set(columns)) != len(columns):
        raise ValueError("invalid feature columns")
    integer(max_values, "fit quota", minimum=1)
    if max_values > MAX_FIT_VALUES:
        raise ValueError("fit quota exceeds supported limit")
    values = [array("d") for _ in columns]
    count = 0
    digest = sha256()
    for sample in samples:
        if (
            sample.split != "train"
            or not sample.eligible
            or (normal_only and not sample.normal_run)
        ):
            continue
        if len(sample.values) != len(columns):
            raise ValueError("training feature shape mismatch")
        count += 1
        if count * len(columns) > max_values:
            raise ValueError("training fit quota exceeded")
        digest.update(canonical_bytes(integer(sample.row_index, "row index")) + b"\n")
        for vector, value in zip(values, sample.values, strict=True):
            if value is not None:
                vector.append(number(value, "training feature", minimum=-1e100, maximum=1e100))
    if not count:
        raise ValueError("no eligible training rows")
    medians = tuple(float(median(v)) if v else 0.0 for v in values)
    means = tuple(
        (math.fsum(v) + (count - len(v)) * replacement) / count
        for v, replacement in zip(values, medians, strict=True)
    )
    scales = tuple(
        math.sqrt(
            (
                math.fsum((value - mean) ** 2 for value in v)
                + (count - len(v)) * (replacement - mean) ** 2
            )
            / count
        )
        or 1.0
        for v, replacement, mean in zip(values, medians, means, strict=True)
    )
    state = Preprocessor(
        columns,
        medians,
        means,
        scales,
        tuple(len(v) for v in values),
        count,
        normal_only,
        digest.hexdigest(),
    )
    # Validate the serializable contract before any prepared table is published.
    return Preprocessor.from_dict(state.as_dict())
