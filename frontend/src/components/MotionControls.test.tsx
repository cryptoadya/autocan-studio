import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test } from 'vitest'
import { MotionControls } from './MotionControls'
import type { SimState } from '../types'

const state: SimState = {sim_time_ns: 1e9, paused: false, throttle_percent: 50,
  kombi_rpm: 800, kombi_status: 'TIMEOUT', engine_running: true,
  engine_publishing: false, last_received_ns: 0, frames_published: 1, deliveries: 1,
  brake_percent: 0, gear: 'D', physical_speed_kph: 10.26, shift_rejection: null}

test('speed is explicitly physical model telemetry rather than received CAN', () => {
  render(<MotionControls state={state} disabled={false} onBrake={() => {}} onGear={() => {}} />)
  expect(screen.getByRole('region', {name: 'Motion · physical model'})).toBeVisible()
  expect(screen.getByTestId('physical-speed')).toHaveTextContent('10.3')
  expect(screen.getByText(/not a CAN measurement/i)).toBeVisible()
})

test('brake and gear controls submit user input and explain rejected shifts', async () => {
  let brake = 0
  let gear = ''
  render(<MotionControls state={{...state, shift_rejection: 'vehicle_moving'}} disabled={false}
    onBrake={value => {brake = value}} onGear={value => {gear = value}} />)
  const user = userEvent.setup()
  const control = screen.getByRole('slider', {name: 'Brake'})
  fireEvent.change(control, {target: {value: '100'}})
  fireEvent.keyUp(control, {key: 'End'})
  expect(brake).toBe(100)
  await user.selectOptions(screen.getByRole('combobox', {name: 'Gear'}), 'N')
  expect(gear).toBe('N')
  expect(screen.getByRole('status')).toHaveTextContent('Stop the vehicle')
})

test('playback disables motion controls and preserves signed reverse speed', () => {
  render(<MotionControls state={{...state, physical_speed_kph: -3.6, gear: 'R'}} disabled
    onBrake={() => {throw Error('live input')}} onGear={() => {throw Error('live input')}} />)
  expect(screen.getByRole('slider', {name: 'Brake'})).toBeDisabled()
  expect(screen.getByRole('combobox', {name: 'Gear'})).toBeDisabled()
  expect(screen.getByTestId('physical-speed')).toHaveTextContent('-3.6')
})
