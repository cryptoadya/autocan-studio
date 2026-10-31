import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test } from 'vitest'
import { FaultLab } from './FaultLab'
import type { FaultCatalog, FaultSpec, Snapshot } from '../types'

const catalog: FaultCatalog = {
  types: [{id: 'signal_spike', name: 'Signal spike', requires_signal: true, wheel_only: false,
    parameters: [{name: 'value', label: 'Value', minimum: -10000, maximum: 10000, step: .01,
      default: 7000, scale: 1, optional: false}]},
  {id: 'message_delay', name: 'Message delay', requires_signal: false, wheel_only: false,
    parameters: [{name: 'min_ns', label: 'Minimum delay (ms)', minimum: 0, maximum: 1000, step: 1,
      default: 0, scale: 1e6, optional: false},
    {name: 'max_ns', label: 'Maximum delay (ms)', minimum: 0, maximum: 1000, step: 1,
      default: 60, scale: 1e6, optional: false}]}],
  messages: [{can_id: 512, name: 'EngineStatus', ecu_id: 'engine',
    signals: [{name: 'EngineRpm', minimum: 0, maximum: 8000, step: .25, unit: 'rpm'}]}],
}
const view: Snapshot = {id: 'run', mode: 'live', sim_time_ns: 150e6, stopped: false,
  recording: 'recording', error: null, frames: [], signals: [],
  state: {sim_time_ns: 150e6, paused: true, throttle_percent: 0, kombi_rpm: 7000,
    kombi_status: 'FRESH', engine_running: true, engine_publishing: true,
    last_received_ns: 140e6, frames_published: 1, deliveries: 1},
  active_faults: [{fault: {fault_id: 'spike', type: 'signal_spike', target_ecu: 'engine',
    target_message_id: 512, signal: 'EngineRpm', parameters: {value: 7000}, seed: 0}, start_at_ns: 100e6}],
  timeline: [{event_seq: 2, sim_time_ns: 100e6, event_kind: 'FaultActivated',
    title: 'Fault activated', details: 'spike · signal_spike · engine', fault_id: 'spike'}],
  timeline_truncated: 4,
}

test('fault form uses DBC bounds and submits spike parameters', async () => {
  let submitted: FaultSpec | undefined
  render(<FaultLab catalog={catalog} view={view} disabled={false}
    onInject={fault => {submitted = fault}} onClear={() => {}} onSeek={() => {}} />)
  const user = userEvent.setup()
  expect(screen.getByRole('spinbutton', {name: 'Value (rpm)'})).toHaveAttribute('max', '8000')
  await user.click(screen.getByRole('button', {name: 'Inject fault'}))
  expect(submitted).toEqual({fault_id: 'manual-1', type: 'signal_spike', target_ecu: 'engine',
    target_message_id: 512, signal: 'EngineRpm', parameters: {value: 7000}, seed: 0})
})

test('delay controls convert milliseconds into canonical integer nanoseconds', async () => {
  let submitted: FaultSpec | undefined
  render(<FaultLab catalog={catalog} view={view} disabled={false}
    onInject={fault => {submitted = fault}} onClear={() => {}} onSeek={() => {}} />)
  const user = userEvent.setup()
  await user.selectOptions(screen.getByRole('combobox', {name: 'Fault type'}), 'message_delay')
  const maximum = screen.getByRole('spinbutton', {name: 'Maximum delay (ms)'})
  await user.clear(maximum)
  await user.type(maximum, '25')
  await user.click(screen.getByRole('button', {name: 'Inject fault'}))
  expect(submitted?.parameters).toEqual({min_ns: 0, max_ns: 25e6})
  expect(submitted?.signal).toBeUndefined()
})

test('active faults clear individually; playback disables mutation and seeks timeline', async () => {
  const cleared: string[] = []
  const sought: number[] = []
  const {rerender} = render(<FaultLab catalog={catalog} view={view} disabled={false}
    onInject={() => {}} onClear={id => cleared.push(id)} onSeek={time => sought.push(time)} />)
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', {name: 'Clear spike'}))
  expect(cleared).toEqual(['spike'])
  expect(screen.getByText('4 earlier events omitted')).toBeVisible()
  rerender(<FaultLab catalog={catalog} view={{...view, mode: 'playback', recording: 'saved', stopped: true}} disabled
    onInject={() => {throw Error('live mutation')}} onClear={() => {throw Error('live mutation')}}
    onSeek={time => sought.push(time)} />)
  expect(screen.getByRole('button', {name: 'Inject fault'})).toBeDisabled()
  expect(screen.getByRole('button', {name: 'Clear spike'})).toBeDisabled()
  const timeline = screen.getByRole('region', {name: 'Fault timeline'})
  await user.click(within(timeline).getByRole('button', {name: 'Seek to 0.100 s'}))
  expect(sought).toEqual([100e6])
})

test('a capture-mode stuck fault is described as a held signal', () => {
  render(<FaultLab catalog={catalog} view={{...view, active_faults: [{
    fault: {...view.active_faults![0]!.fault, type: 'signal_stuck', parameters: {}}, start_at_ns: 100e6,
  }]}} disabled={false} onInject={() => {}} onClear={() => {}} onSeek={() => {}} />)
  expect(screen.getByText(/Captured at first publication/)).toBeVisible()
  expect(screen.queryByText(/Publication suppressed/)).not.toBeInTheDocument()
})
