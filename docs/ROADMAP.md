# Roadmap

## Available today

- The [v1.0.0 release](https://github.com/cryptoadya/autocan-studio/releases/tag/v1.0.0)
  includes the five-ECU network, interactive faults, Parquet exchange, and
  seeded dataset generation.
- The default branch adds offline detector evaluation and
  [live Random Forest scoring and recorded playback](LIVE_DEMO.md).
  See the [changelog](../CHANGELOG.md#unreleased) for unreleased changes.

## Potential next steps

- **Broader synthetic evaluation.** Increase normal-driving exposure and
  variation, then measure false alarms and detection across more independent runs.
- **Packaged dashboard distribution.** Make the local UI available without a
  source checkout or a local frontend build.

These capabilities are future work. The current evaluation and its limits are
documented in [ML evaluation](ML_EVALUATION.md).
