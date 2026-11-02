# Frozen OOD fault and normal evaluation — M8 third pass

Two separate held-out domains extend the [fixed Tier 0 benchmark](BENCHMARK.md).
`ood_fault` contains fault **types** absent from the entire ID collection, on
ID-known driving families. `ood_normal` contains fault-free driving families
absent from that ID collection. Both use the same observer, physics, DBC, duration
range, causal features, three rules and episode evaluator as ID.

This pass reserves existing synthetic mode families; it does not add new physics,
vehicle loads or a new normal-driving generator. It demonstrates these specific
distribution shifts, not arbitrary unknown faults, parameter-only OOD, or
generalization to real CAN/vehicles. ML, parameter selection, window-level AP,
bootstrap uncertainty and runtime/RSS evaluation remain separate work.

## Prepare ID before OOD

Reserve normal families **before** producing the ID benchmark. For example:

```sh
uv run --locked autocan generate --output data/datasets/m8-ood-id --runs 18 --fault-runs 6 --modes city acceleration --fault-types ecu_offline signal_spike cross_signal_inconsistency --seed 42
uv run --locked autocan benchmark data/datasets/m8-ood-id --output data/benchmarks/m8-ood-id --split-seed 42
uv run --locked autocan generate-ood data/datasets/m8-ood-id --reference data/benchmarks/m8-ood-id --output data/datasets/m8-ood --seed 43
uv run --locked autocan benchmark-ood data/datasets/m8-ood-id data/datasets/m8-ood --reference data/benchmarks/m8-ood-id --output data/benchmarks/m8-ood
```

Use new destinations for each operation. A previous ID collection containing all
six normal families cannot support this family-held-out normal test; generation
fails rather than calling new seeds OOD. Similarly, at least one library fault
type must remain absent from ID. ID includes train, validation **and** test here;
absence is checked against the entire verified collection, a stronger exclusion
than train/validation alone.

The example reserves idle, stop-and-go, cruise and braking as normal OOD. Its unseen
fault candidates are message drop, delay, stuck, bias and frequency anomaly.
Candidates derive from actual verified ID run rows, not unused configuration
options or user-supplied labels. All available absent types/families are candidates;
the M7 balanced seeded generator chooses which occur. A small run count may not
cover every candidate; saved per-run data and per-type metrics state actual coverage.

`generate-ood` options are `--seed` (43), `--fault-runs` (6), `--normal-runs` (6)
and `--max-bytes` (256 MiB). Each domain accepts 1–300 runs, subject to quotas.
Fault runs each contain one injected/cleared fault; normal runs contain no
intervention. Independent domain seeds derive from the OOD seed. No failed
isolation check is silently resampled. For example, an unobservable stuck signal
can yield an input exactly identical to an ID run; that candidate suite is rejected.

## Frozen configuration and integrity

The required reference is a completed ID benchmark over the exact supplied ID
collection. Generation verifies that collection, the benchmark's artifact
checksums, source identity, whole-run split, feature configuration, versions and
current feature/evaluator/benchmark code hash. It writes `protocol.json` **before**
generating any OOD run. The protocol binds physical ID/reference manifest hashes,
logical ID identity, all feature settings, domain recipes, seeds, counts and quotas.

`benchmark-ood` requires the original ID collection and benchmark again. It
revalidates the protocol's exact binding, all nested M7 artifacts and seeded
recipes before evaluating either domain. A different valid ID benchmark with
different thresholds is rejected for that existing suite. There are no window,
heartbeat or threshold flags on `benchmark-ood`; its only result-control option
is `--max-bytes` (16 MiB, up to 64 MiB).

There is no optimization, ranking, threshold sweep, validation fitting or
model/parameter selection on OOD. The settings come solely from the pre-existing
ID reference; OOD metrics cannot change them. Do not manually choose references,
seeds or settings based on favorable OOD results and present them as untouched
holdouts. Integrity checks are not signatures or proof of a user's experiment
history. The reference's recorded configuration is the explicit protocol.

Run identities, exact scenario hashes and exact observer-stream semantic hashes
must be disjoint across ID, fault OOD and normal OOD. Duplicate identities within
a domain are rejected too. Exact duplicate inputs within one held-out domain
are allowed; near-duplicate/family independence is not inferred from hashes.
Broad type/family holds are declared by the protocol, without pretending that
existing synthetic families represent all normal domain shifts.

Fault markers, known-mode/type membership, collection names, seeds, split/domain
labels and provenance are evaluator/generator context only. `_analyze_run` receives
the frozen configuration and wire-observation stream; its detector finishes
before the evaluator reads fault ground truth. No OOD label or true physical
signal enters a feature or score.

## Separate results

`metrics.json` contains six `(domain, rule)` rows. ID metrics are not pooled into
either OOD domain, and the two OOD populations are not averaged together.

- Fault OOD reports episode detection, precision/F1, latency mean/p50/p95 and
  per-fault-type counts/rates using the existing target-aware one-to-one policy
  and one-window post-clear grace. All its runs have interventions, so normal
  exposure is zero and false alarms/hour is `null` for this domain.
- Normal OOD reports false alarm episodes, pooled normal exposure and false
  alarms/hour for each rule. With no truth faults, detection rate and fault
  latency are `null`; an absence of alarms is not evidence of fault-detection
  ability or a guarantee of low operational false positives.

Warm-up, missing evidence, episode grouping, censoring and discarded partial
windows are unchanged from BENCHMARK.md. Small denominators and zero exposure
remain explicit. Novel faults can be entirely invisible or missed by all three
rules; such results are retained. The normal OOD FA/hour denominator includes
only complete evaluated windows from the fault-free normal collection.

## Files, provenance and resource limits

The generated suite contains `protocol.json`, `manifest.json` and two complete
M7 collections under `ood_fault/` and `ood_normal/`. It does not modify or copy
the ID collection/reference. Keep those inputs available for later verification.
The suite manifest inventories all protocol and nested artifacts and records a
logical suite hash. Repeating the same frozen reference and OOD recipe preserves
that logical hash while newly assigned run IDs/physical hashes may differ.

Evaluation creates `protocol.json`, `runs.jsonl`, `metrics.json`, `report.md` and
`manifest.json`. Per-run evidence includes domain, exposure, censored truth,
rule episodes, match indices and latency. Provenance records frozen reference,
suite manifest hash, current code/Git/package versions and artifact hashes.
Output paths and wall-clock timestamps do not enter metrics. Re-evaluation of
the same inputs/settings/code/environment is reproducible while Git provenance
is unchanged; it does not retune anything.

The suite quota includes all nested artifacts and manifests, range 8192 bytes–
1 GiB. Each generated collection receives half that budget; M7 per-run limits,
bounded row groups and feature/episode quotas still apply. Metadata limits and
bounded buffer/manifest overshoot follow M7. Results have a separate quota.
Inputs are verified against a fixed, symlink-checked inventory. Validation/quota
failures before publication clean staging and publish no destination. As with
the existing M7 publisher, an I/O error during file-by-file publication or a crash
can leave an incomplete destination: a completion manifest is required, and an
incomplete folder must be moved/discarded before retry. Nothing is overwritten.
