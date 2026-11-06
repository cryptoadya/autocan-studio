# Dataset Factory verification

## Initial implementation acceptance

The implementation pass ran `uv run --locked pytest` (324 passed), Ruff lint
and formatting checks, strict mypy and `uv build --no-sources` successfully.
Focused coverage includes physical modes at both 8 s and 30 s, all eight fault
types with fresh rerun, repeated-series semantic equality, Parquet reload,
receiver input separation, failed-quota cleanup, corruption and symlink rejection.
The default 24-run collection was also generated and verified: four trips per
mode, six fault runs, 384 artifacts and 68,080,321 bytes including its manifest.
Generated data stays ignored under `data/`. Frontend/browser checks were not run
for this CLI-only pass; no remote CI or release result is claimed.

The subsequent [M7 final acceptance](M7_ACCEPTANCE.md) adds the provenance fixes,
full collection round trips/reruns and current Python/frontend/browser results.
