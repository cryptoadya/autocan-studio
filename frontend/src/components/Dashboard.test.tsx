import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test } from 'vitest'
import { Dashboard } from './Dashboard'
import type { SimState } from '../types'

const state: SimState = { sim_time_ns: 300_000_000, paused: false, throttle_percent: 50,
  kombi_rpm: 1000, kombi_status: 'TIMEOUT', engine_running: true,
  engine_publishing: false, last_received_ns: 100_000_000, frames_published: 6, deliveries: 6 }

test('timed out RPM keeps last received value and its explicit freshness label', () => {
  render(<Dashboard state={state} disabled={false} onThrottle={() => {}} onOffline={() => {}} />)
  expect(screen.getByTestId('received-rpm')).toHaveTextContent('1,000')
  expect(screen.getByText('TIMEOUT')).toBeVisible()
  expect(screen.getByText(/last received measurement/i)).toBeVisible()
  expect(screen.getByRole('button', {name: 'Bring Engine ECU online'})).toBeEnabled()
})

test('playback controls cannot send throttle or Offline commands', () => {
  render(<Dashboard state={state} disabled onThrottle={() => {throw Error('live command')}}
    onOffline={() => {throw Error('live command')}} />)
  expect(screen.getByRole('slider', {name: 'Throttle'})).toBeDisabled()
  expect(screen.getByRole('button', {name: 'Bring Engine ECU online'})).toBeDisabled()
})

test('throttle commits a real edited percentage and Offline sends the desired fault', async () => {
  let throttle = 0
  let offline: boolean | undefined
  render(<Dashboard state={{...state, engine_publishing: true}} disabled={false}
    onThrottle={value => {throttle = value}} onOffline={value => {offline = value}} />)
  const user = userEvent.setup()
  const slider = screen.getByRole('slider', {name: 'Throttle'})
  slider.focus()
  fireEvent.change(slider, {target: {value: '51'}})
  fireEvent.keyUp(slider, {key: 'ArrowRight'})
  expect(throttle).toBe(51)
  await user.click(screen.getByRole('button', {name: 'Take Engine ECU offline'}))
  expect(offline).toBe(true)
})
