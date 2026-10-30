import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { NetworkPanel } from './NetworkPanel'
import type { SimState } from '../types'

const state: SimState = {
  sim_time_ns: 0, paused: false, throttle_percent: 50, kombi_rpm: 900, kombi_status: 'FRESH',
  engine_running: true, engine_publishing: true, last_received_ns: 0, frames_published: 8, deliveries: 27,
  physical_speed_kph: 90, kombi_speed_kph: 12.3, kombi_speed_status: 'TIMEOUT',
  kombi_temperature_c: 20.1, kombi_temperature_status: 'FRESH', kombi_gear: 'D', kombi_gear_status: 'FRESH',
  kombi_ignition: 'ON', kombi_body_status: 'FRESH', kombi_abs_status: 'TIMEOUT', kombi_wheels_consistent: true,
  abs_publishing: false,
}

describe('CAN network panel', () => {
  it('holds delivered CAN values independently of model telemetry and publication state', () => {
    const onOffline = vi.fn()
    render(<NetworkPanel state={state} disabled={false} onOffline={onOffline} />)
    expect(screen.getByTestId('can-speed')).toHaveTextContent('12.3 km/h')
    expect(screen.getByTestId('can-temperature')).toHaveTextContent('20.1 °C')
    expect(screen.getByTestId('can-gear')).toHaveTextContent('D')
    fireEvent.click(screen.getByRole('button', {name: 'Resume ABS/DSC publication'}))
    expect(onOffline).toHaveBeenCalledWith('abs', false)
  })
  it('disables all publication controls in playback and shows unavailable values', () => {
    render(<NetworkPanel state={{...state, kombi_speed_kph: null}} disabled onOffline={vi.fn()} />)
    expect(screen.getByTestId('can-speed')).toHaveTextContent('— km/h')
    for (const button of screen.getAllByRole('button')) expect(button).toBeDisabled()
  })
})
