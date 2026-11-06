import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import type { Snapshot, TimelineEvent } from '../types'
import { AnomalyChart } from './AnomalyChart'

test('an active fault stays on the chart after the journal evicts its activation', () => {
  const marker: TimelineEvent = {event_seq: 1, sim_time_ns: 0, start_at_ns: 0,
    event_kind: 'FaultActivated', fault_id: 'abs-offline', title: 'Fault activated', details: 'ABS offline'}
  const view: Snapshot = {
    id: 'run', mode: 'live', sim_time_ns: 5e9, stopped: false, recording: 'recording', error: null,
    frames: [], signals: [], state: {sim_time_ns: 5e9, paused: false, throttle_percent: 0,
      kombi_rpm: null, kombi_status: 'UNAVAILABLE', engine_running: true, engine_publishing: true,
      last_received_ns: null, frames_published: 0, deliveries: 0},
    timeline: [marker], active_fault_markers: [marker],
    anomaly: {model: {model_id: 'random_forest', model_sha256: 'a'.repeat(64), threshold: 0.5,
      feature_schema_version: 1}, scores: [], alerts: []},
  }
  const {rerender} = render(<AnomalyChart view={view} />)
  expect(screen.getAllByTestId('ground-truth-fault')).toHaveLength(1)
  rerender(<AnomalyChart view={{...view, timeline: [], timeline_truncated: 1}} />)
  expect(screen.getByTestId('ground-truth-fault')).toHaveTextContent('ABS offline')
})
