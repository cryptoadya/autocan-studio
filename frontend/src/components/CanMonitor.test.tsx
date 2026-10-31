import { render, screen, within } from '@testing-library/react'
import { expect, test } from 'vitest'
import { CanMonitor } from './CanMonitor'

test('network monitor distinguishes delivery delay from frame creation', () => {
  render(<CanMonitor network skipped={0} frames={[{frame_id: 1, event_seq: 1,
    sim_time_ns: 60e6, created_at_ns: 0, can_id: 512, dlc: 2, payload_hex: '800c', rpm: 800}]} />)
  expect(screen.getByRole('columnheader', {name: 'DELAY / ms'})).toBeVisible()
  expect(within(screen.getAllByRole('row')[1]!).getByRole('cell', {name: '60.000'})).toBeVisible()
})
