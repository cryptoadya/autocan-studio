# Parquet trace exchange and observer datasets (M7 first pass)

This pass adds bounded, headless storage primitives. JSONL remains the live
recording and playback format. Dataset Factory driving-mode generation is the
next M7 pass; features, splits, baselines and ML belong to M8 and later.

## Reproduce a round trip

From a source checkout with the locked Python dependencies installed:

```sh
uv run --locked autocan run scenarios/timing_jitter.json --output data/runs/jitter
uv run --locked autocan trace-export data/runs/jitter --output data/parquet/jitter
uv run --locked autocan trace-import data/parquet/jitter --output data/runs/jitter-restored
uv run --locked autocan playback data/runs/jitter-restored
uv run --locked autocan rerun data/runs/jitter-restored
uv run --locked autocan dataset data/runs/jitter-restored --receiver observer --output data/datasets/jitter
```

Every output must be a new directory. There is no overwrite switch. CLI failures
return a nonzero exit code with an error on stderr. These operations do not
start an API server or require the browser. Dashboard integration with these new
commands is outside this pass.

## Exchange format v1

Each bundle represents one run (use separate run directories when collecting
multiple runs). Its manifest identifies `autocan-parquet`, format version 1 and
complete status. It embeds the original trace manifest without changing run
identity, configuration, initial/final state, seed, RNG streams, scenario
provenance, core/package/profile versions, checksums or semantic hash.

| Artifact | Content |
| --- | --- |
| `frames.parquet` | Typed immutable publications: event sequence/time, frame ID, source ECU, channel, CAN ID, flags, DLC, binary payload and creation time |
| `deliveries.parquet` | Typed per-receiver receipts: event sequence/time, frame ID, receiver ID and receipt time |
| `events.parquet` | Other canonical events (including physical samples and ECU status transitions), as sequence plus lossless canonical JSON |
| `commands.parquet` | Applied commands, as sequence plus canonical JSON |
| `labels/faults.parquet` | Fault activation/clear and transport-drop ground truth, separately from observations |
| `profile.dbc` | Exact saved DBC snapshot |

Tables use explicit nonnullable schemas and integer nanoseconds. They are written
without compression or dictionaries, in groups of at most 256 rows. Export
streams the existing event files and never builds a whole-run dataframe. Empty
tables retain their schemas. Tables have physical SHA256/byte/record metadata
and a semantic SHA256 over ordered canonical rows (binary payloads represented
as lowercase hex). The original canonical event hash is independent of the
Parquet container bytes.

Import first validates the manifest, fixed artifact paths, physical sizes and
checksums. It rejects symlink artifacts, unsupported schemas, null values,
oversized row groups/decoded data, invalid event ordering and altered semantic
content. It merges table iterators by canonical sequence into staged JSONL and
runs the existing trace verifier before publishing the restored run. This also
checks original JSONL checksums, frame/delivery references and the complete
canonical semantic hash. Playback does not simulate physics. A separate rerun
constructs a fresh simulation and compares events/final state; it requires the
exact supported core, profile and runtime versions.

JSONL format/schema version 1 remains supported for both DemoCar profiles
(`democar-0.1.0` and `democar-0.2.0`). Historical runs can be exchanged and played
back even when the current core cannot rerun them. Exchange does not upgrade or
rewrite historical provenance.

## Observer dataset v1

`dataset` consumes a verified JSONL run, selecting `--receiver` explicitly
(default `KOMBI`). The network profile supports its six declared receivers,
including the independent `observer`; the legacy profile supports `KOMBI`.
Unknown receivers fail before producing an output.

`observations.parquet` contains only these fields:

| Field | Meaning |
| --- | --- |
| `received_at_ns` | This receiver's virtual delivery time |
| `channel` | Observed CAN channel |
| `can_id` | Standard 11-bit arbitration ID |
| `is_extended_id` | CAN ID flag (false for the supported profiles) |
| `dlc` | Payload byte count |
| `payload` | Raw binary payload |

Rows follow receipt order, including reordered delayed frames. Publications
that are dropped, still pending at the end, or not subscribed by this receiver
produce no observation. A bounded frame lookup is retired on each selected
receipt/drop; it does not retain the whole trace. An entirely silent receiver
produces a valid empty table.

`labels/faults.parquet` contains fault activation/clear events with their original
specifications and times, including faults still active when the run ends. It
does not invent cleared intervals. The dataset manifest stores selected receiver,
source provenance, end time, schema version, file checksums and semantic hashes.
These are metadata for reproducibility, **not feature columns**. Frame creation
time, source ECU, frame/event IDs, physical state, driver commands, faults,
scenario names, seeds and labels do not enter the observation table. There are
no feature windows, frame classifications, train/test splits or models yet.

## Bounds and publication

`--max-bytes` accepts 4096 bytes through 16 MiB. The default is 16 MiB. Export
counts its manifest and all artifacts; import bounds both the complete bundle
and reconstructed trace artifacts. Parquet footers and decoded table sizes
are bounded separately (64 MiB decoded per table, at most 256 rows per group).
The live JSONL recorder retains its existing 16 MiB limit. An export may need
more space than its source JSONL because of table metadata and provenance;
reduce run duration when it cannot fit the limit.

Conversion stages in a private temporary sibling directory and cleans staging
on failure. The destination is reserved exclusively only after successful
conversion/verification; its complete manifest is published last. No existing
file/directory is replaced, and quota failure publishes no destination. Temporary
disk use includes the source and one staged output; a failed export can briefly
exceed its output quota by buffered row groups/footers before it is removed.
There is no automatic deletion, SQLite index, compressed archive import, API/UI
upload, or V1 release in this pass.
