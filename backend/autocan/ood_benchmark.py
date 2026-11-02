"""Score frozen OOD domains without accepting or selecting new thresholds."""

from dataclasses import asdict
from hashlib import sha256
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

from autocan.benchmark import DEFAULT_BENCHMARK_BYTES, MAX_BENCHMARK_BYTES, _analyze_run, _report
from autocan.evaluation import EVALUATOR_VERSION, RULE_IDS, RuleResult, summarize
from autocan.features import DETECTOR_ID, FEATURE_SCHEMA_VERSION
from autocan.ood import DOMAINS, OOD_VERSION, feature_configuration, verify_ood
from autocan.parquet import _file_hash, _new_output
from autocan.traces import TraceError, _git_provenance, _packages
from autocan.validation import canonical_bytes, integer


def evaluate_ood(
    source: Path,
    reference: Path,
    suite: Path,
    target: Path,
    *,
    max_bytes: int = DEFAULT_BENCHMARK_BYTES,
) -> dict[str, object]:
    if integer(max_bytes, "OOD result quota", minimum=4096) > MAX_BENCHMARK_BYTES:
        raise ValueError("OOD result quota must be <= 64 MiB")
    with _new_output(target) as staging:
        protocol, collections = verify_ood(source, reference, suite)
        config = feature_configuration(protocol["feature_configuration"])
        used = 0

        def reserve(raw: bytes) -> None:
            nonlocal used
            used += len(raw)
            if used > max_bytes:
                raise TraceError("OOD result quota exceeded")

        def save(name: str, raw: bytes) -> None:
            reserve(raw)
            (staging / name).write_bytes(raw)

        save("protocol.json", canonical_bytes(protocol) + b"\n")
        digest = sha256(
            canonical_bytes(
                {
                    "protocol": protocol,
                    "suite_semantic_sha256": {
                        domain: collections[domain]["semantic_sha256"] for domain in DOMAINS
                    },
                }
            )
        )
        pooled: dict[tuple[str, str], list[RuleResult]] = {
            (d, r): [] for d in DOMAINS for r in RULE_IDS
        }
        with (staging / "runs.jsonl").open("wb") as stream:
            for domain in DOMAINS:
                for run in collections[domain]["runs"]:
                    result, rules = _analyze_run(suite / domain / run["path"], run, config)
                    result["split"] = domain
                    raw = canonical_bytes(result) + b"\n"
                    reserve(raw)
                    stream.write(raw)
                    digest.update(
                        canonical_bytes({k: v for k, v in result.items() if k != "run_id"}) + b"\n"
                    )
                    for rule, rule_result in rules.items():
                        pooled[domain, rule].append(rule_result)
        rows = [
            {"split": domain, "rule_id": rule, **summarize(pooled[domain, rule])}
            for domain in DOMAINS
            for rule in RULE_IDS
        ]
        metrics = {
            "format": "autocan-ood-metrics",
            "format_version": 1,
            "evaluator_version": EVALUATOR_VERSION,
            "ood_version": OOD_VERSION,
            "detector_id": DETECTOR_ID,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "configuration": asdict(config),
            "reference": protocol["reference"],
            "rows": rows,
        }
        save("metrics.json", canonical_bytes(metrics) + b"\n")
        digest.update(canonical_bytes(metrics))
        report = _report(rows, protocol["reference"]["dataset_semantic_sha256"], config)
        report = report.replace("# Tier 0 episode benchmark", "# Frozen OOD episode benchmark", 1)
        report = report.replace(
            "Run/group splits are fixed in `split.json`; no OOD or ML quality claim is made.",
            "OOD domains are fixed in `protocol.json`; no fitting or threshold selection uses OOD.",
        )
        report += (
            "\n`ood_fault` holds out fault types on ID-known mode families. "
            "`ood_normal` holds out normal mode families with no injected faults.\n"
            "The dataset hash above identifies the ID reference. Domains are reported separately; "
            "small synthetic samples do not establish real-vehicle generalization.\n"
        )
        save("report.md", report.encode())
        code = sha256()
        for name in ("features", "evaluation", "benchmark", "ood", "ood_benchmark"):
            code.update(
                name.encode() + b"\0" + files("autocan").joinpath(f"{name}.py").read_bytes()
            )
        manifest: dict[str, object] = {
            "format": "autocan-ood-benchmark",
            "format_version": 1,
            "status": "complete",
            "ood_version": OOD_VERSION,
            "evaluator_version": EVALUATOR_VERSION,
            "detector_id": DETECTOR_ID,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "configuration": asdict(config),
            "reference": protocol["reference"],
            "suite_manifest_sha256": _file_hash(suite / "manifest.json"),
            "implementation_sha256": code.hexdigest(),
            "package_versions": {**_packages(), "pyarrow": version("pyarrow")},
            **_git_provenance(),
            "semantic_sha256": digest.hexdigest(),
            "artifacts": {
                path.name: {"bytes": path.stat().st_size, "sha256": _file_hash(path)}
                for path in sorted(staging.iterdir())
            },
        }
        save("manifest.json", canonical_bytes(manifest) + b"\n")
    return manifest
