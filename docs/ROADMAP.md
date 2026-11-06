# Roadmap

## Available today

- The [v0.1.0 release](https://github.com/cryptoadya/autocan-studio/releases/tag/v0.1.0)
  includes the engine-to-cluster simulation, local dashboard, fault demo,
  recordings, playback, and deterministic reruns.
- The default branch adds the five-ECU network, interactive fault scenarios,
  Parquet exchange, seeded dataset generation, and offline detector evaluation.
  See the [changelog](../CHANGELOG.md#unreleased) for unreleased changes.

## Potential next steps

- **Live scoring.** Run the causal feature pipeline against live observer
  deliveries and surface detector alerts alongside CAN measurements. Check
  feature and score parity against the offline path.
- **Broader synthetic evaluation.** Increase normal-driving exposure and
  variation, then measure false alarms and detection across more independent runs.
- **Packaged dashboard distribution.** Make the local UI available without a
  source checkout or a local frontend build.

These capabilities are future work. The current evaluation and its limits are
documented in [ML evaluation](ML_EVALUATION.md).
