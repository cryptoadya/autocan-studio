# Fixed Tier 0 benchmark — M8 second pass

Evaluate the three [causal baseline rules](BASELINES.md) on an existing verified
M7 [Dataset Factory collection](../DATA_FORMATS.md#dataset-factory). This pass adds whole-run
partitions, episode matching, detection latency and false alarm episodes/hour.
Thresholds are fixed before scoring. It does not fit a model or select thresholds.
The [third M8 pass](OOD.md) adds separately generated/evaluated OOD fault and
normal sets bound to this frozen ID benchmark; broader M8/ML acceptance is separate.

## Reproduce

```sh
uv run --locked autocan generate --output data/datasets/m8-rules --runs 24 --fault-runs 9 --fault-types ecu_offline signal_spike cross_signal_inconsistency --seed 42
uv run --locked autocan benchmark data/datasets/m8-rules --output data/benchmarks/rules-42 --split-seed 42
uv run --locked autocan benchmark data/datasets/m8-rules --output data/benchmarks/rules-42-repeat --split data/benchmarks/rules-42/split.json
```

All output directories must be new. `benchmark` verifies the entire collection
before reading observer inputs, publishes the completion manifest last, and never
overwrites data. Stdout is one JSON completion record; errors return nonzero.
`source` is a collection root, not one receiver's `observer/` directory.

The feature/rule flags are the same as `baseline`: `--window` (seconds, default
0.5), `--heartbeat-periods` (5), `--rpm-derivative-threshold` (20000 RPM/s) and
`--wheel-residual-threshold` (5 km/h). These remain synthetic demonstration
defaults. `--split-seed` defaults to 42; alternatively `--split` reuses a saved
manifest's seed and assignments. They are mutually exclusive on the CLI.

## Frozen whole-run splits

Splitting happens before any feature extraction or scoring. No windows from
one run can enter more than one partition. Runs sharing an exact scenario hash
**or** an exact whole observer-stream semantic hash form a connected group;
transitive duplicates stay together. Grouping uses no fault labels or mode
stratification. It protects against identical saved inputs, not all near-duplicate
drives; broader scenario families and OOD separation remain future work.

Groups are ordered by run index, shuffled with the recorded seed, then assigned
`max(1, floor(groups / 5))` each to test and validation; the rest go to train.
This targets roughly 60/20/20 with integer rounding (24 independent groups give
16/4/4). At least three independent groups and unique run IDs/indices are
required. A large duplicate group can skew run counts. Splits need not contain
every fault type or any normal run; missing denominators remain explicit.

`split.json` binds dataset semantic identity, each run ID/index, group and
partition. Reuse regenerates and validates the complete assignment: changed
datasets/identities, overlap, omissions and manual reassignment are rejected.
It is a reproducible split contract for this algorithm, not a custom-split import.

All three rules are evaluated separately on train, validation and test using the
same predeclared thresholds. No selection reads those results. A future tuning
experiment must use train/validation only and freeze the configuration before
final test; repeating this command does not make repeated test-driven tuning
valid. This benchmark provides no scaler or fitted state.

## Episodes and matching

Each rule's simultaneous/overlapping message alerts form one rule episode,
including contiguous message handovers at a window boundary. Its onset is the
first opening; it resolves only when all message alerts resolve. Missing evidence
keeps alerts open, following the detector's existing semantics. An alert still
active at run end has `end_ns: null`; the evaluator does not invent recovery.
Each affected CAN ID retains its own first onset so a later message cannot
inherit an unrelated message's earlier detection time.

Ground truth is read separately from the verified trace's fault-event stream
**after** feature/detector replay. Activation/clear events are paired by fault ID;
uncleared faults are censored at run end. Frame-drop markers are not additional
fault episodes. Message-targeted faults map to their CAN ID; ECU-wide faults map
to that publisher's DBC IDs. All positive-duration fault interventions in the
evaluated span count in each rule's denominator, even when the selected receiver
cannot see them or a rule is unsuitable. Results describe detection of these
synthetic interventions, not diagnosis or observability guarantees.

For each run and rule independently:

1. Visit alert episodes in onset order, breaking ties by saved order.
2. An episode already open before a fault cannot detect it. Affected CAN IDs
   must intersect. The first onset for a matching CAN ID must be in
   `[fault.start, fault.end + window)`; the upper boundary is excluded.
3. If multiple unpaired faults qualify, select the earliest end-plus-grace,
   then start, fault ID and saved order. One alert receives at most one credit;
   each fault receives at most one credit per rule. This deterministic greedy
   policy does not claim globally optimal matching for arbitrary overlapping faults.
4. Latency is matching-CAN onset minus fault start. Never use a negative latency
   or credit an unrelated CAN ID. Later alerts for an already matched fault are
   unmatched false positives under this policy.

The post-clear grace is exactly one feature window because a short anomaly can
be reported only at window closure. It affects evaluator matching only; it does
not change causal features or thresholds. Window-end timestamp quantization,
thresholds and median-wheel/common-mode limitations remain those in BASELINES.md.

## Metrics and exposure

Each `(split, rule)` row pools counts across its runs:

- Detection rate = matched faults / fault episodes; missed = faults − matched.
- Episode precision = matched / alert episodes; unmatched alerts are false
  positive episodes. Episode F1 = `2 * matched / (faults + alerts)`.
- Detection latency mean and nearest-rank p50/p95 in integer nanoseconds
  (mean can be fractional), plus detection counts/rates/latency per fault type.
- **False alarms/hour = normal-run alert episodes × 3,600,000,000,000 /
  normal evaluated nanoseconds.** Only entirely fault-free runs contribute;
  neither quiet segments of fault runs nor fault-run time inflate this exposure.

No denominator means JSON `null` / report `n/a`, not a zero rate. With faults
but no alerts, F1 is zero and precision is unavailable. Rates pool total counts
and exposure; they never average per-run rates or infer TN/accuracy from silence.

Only complete windows are scored. Exposure is `[0, floor(duration/W) * W]`:
the first full window is eligible and includes the detector's existing per-message
heartbeat warm-up; no additional common warm-up is removed. The partial trailing
window contributes neither alerts nor normal exposure. Truth is clipped to that
span and censored when needed; faults beginning after it are reported as unscored.
Runs shorter than one window have zero exposure and no score. A run with any
recorded intervention is never reclassified as normal because its fault is in
an unscored tail. FA/hour over seconds of synthetic traffic is a small-sample
rate, not a validated operational guarantee.

## Artifacts and limits

| File | Contents |
| --- | --- |
| `split.json` | Frozen run identities, duplicate groups and partition assignments |
| `runs.jsonl` | Run exposure, scored truth, rule episodes, match indices/latencies and per-run metrics |
| `metrics.json` | Nine split/rule rows with counts, pooled rates, latency and per-fault-type results |
| `report.md` | Readable comparison table with explicit normal exposure and limitations |
| `manifest.json` | Dataset/source identity, feature/detector/evaluator versions, config, code hash, Git/package provenance and artifact checksums |

No wall-clock timestamp or absolute source/output path enters results. Repeating
the same source/config/split/code/environment yields the same artifacts while
Git provenance is unchanged. The semantic hash additionally excludes generated
run IDs, so freshly regenerated logically identical collections retain logical
benchmark identity; physical/source manifest hashes and provenance can differ.

One run is replayed at a time using the bounded feature pipeline; output retains
episode/match evidence rather than every feature/score. At most 10,000 rule
episodes per run are accepted. Existing M7 input quotas and feature-window quotas
still apply. `--max-bytes` caps the complete benchmark output including manifests
(default 16 MiB, range 4096 bytes–64 MiB). Validation/quota failures before
publication remove staging without creating the destination. The inherited M7
publisher moves files individually, with the completion manifest last; an I/O
failure during publication or a process crash can leave an incomplete destination.
Do not consume it without a completion manifest; discard or move it aside before
retrying. Runtime/RSS benchmarks, bootstrap confidence intervals,
window-level AP/PR curves, OOD fault/normal sets and ML remain outside this pass.
