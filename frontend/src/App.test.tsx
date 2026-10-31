import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { App } from './App'
import { useStudio } from './store'
import type { Snapshot } from './types'

const live: Snapshot = {
  id: 'live-run', mode: 'live', sim_time_ns: 0, frames: [], signals: [],
  stopped: false, recording: 'recording', error: null,
  state: {
    sim_time_ns: 0, paused: true, throttle_percent: 0, kombi_rpm: null,
    kombi_status: 'UNAVAILABLE', engine_running: true, engine_publishing: true,
    last_received_ns: null, frames_published: 0, deliveries: 0,
  },
}
const saved: Snapshot = {
  ...live, id: 'saved-run', mode: 'playback', stopped: true, recording: 'saved',
  duration_ns: 1_000_000_000,
  state: {...live.state, kombi_rpm: 800, kombi_status: 'FRESH', last_received_ns: 0,
    frames_published: 1, deliveries: 1},
  frames: [{frame_id: 1, event_seq: 1, sim_time_ns: 0, can_id: 512,
    dlc: 2, payload_hex: '800c', rpm: 800}],
  signals: [{sim_time_ns: 0, rpm: 800, status: 'FRESH'}],
}

beforeEach(() => {
  useStudio.getState().clear()
  vi.stubGlobal('ResizeObserver', class {observe() {}; disconnect() {}})
  vi.stubGlobal('WebSocket', class {close() {}})
})
afterEach(() => {vi.unstubAllGlobals(); useStudio.getState().clear()})

test('replacing a run leaves exactly one vehicle dashboard and one motion panel', async () => {
  vi.stubGlobal('fetch', async (path: string) => {
    if (path === '/api/simulations/current') return Response.json(live)
    if (path === '/api/runs') return Response.json([])
    if (path === '/api/scenarios') return Response.json([])
    if (path === '/api/faults') return Response.json({types: [], messages: []})
    throw new Error(`Unexpected request: ${path}`)
  })
  render(<App />)
  await waitFor(() => expect(useStudio.getState().live?.id).toBe('live-run'))
  act(() => useStudio.getState().adopt({...live, id: 'new-run'}))
  expect(screen.getAllByRole('region', {name: 'Vehicle dashboard'})).toHaveLength(1)
  expect(screen.getAllByRole('region', {name: 'Motion · physical model'})).toHaveLength(1)
  expect(screen.getAllByRole('slider', {name: 'Throttle'})).toHaveLength(1)
  expect(screen.getAllByRole('region', {name: 'Scenarios and faults'})).toHaveLength(1)
})

test.each(['response', 'error'])('returning to live ignores a late playback %s', async outcome => {
  let resolveResponse!: (response: Response) => void
  let rejectResponse!: (error: Error) => void
  const delayed = new Promise<Response>((resolve, reject) => {
    resolveResponse = resolve; rejectResponse = reject
  })
  let playbackCalls = 0
  const json = (data: unknown) => Response.json(data)
  vi.stubGlobal('fetch', async (path: string) => {
    if (path === '/api/simulations/current') return json(live)
    if (path === '/api/runs') return json([{id: saved.id, duration_ns: saved.duration_ns}])
    if (path === '/api/scenarios') return json([])
    if (path === '/api/faults') return json({types: [], messages: []})
    if (path === '/api/runs/saved-run/playback') {
      playbackCalls++
      return playbackCalls === 1 ? json(saved) : delayed
    }
    throw new Error(`Unexpected request: ${path}`)
  })
  const user = userEvent.setup()
  render(<App />)
  await user.selectOptions(await screen.findByRole('combobox', {name: 'Saved recording'}), saved.id)
  await user.click(screen.getByRole('button', {name: 'Load playback'}))
  await screen.findByText('PLAYBACK', {selector: '.mode'})
  await user.click(screen.getByRole('button', {name: 'Load playback'}))
  await waitFor(() => expect(playbackCalls).toBe(2))
  await user.click(screen.getByRole('button', {name: 'Return to live'}))
  expect(screen.getByText('LIVE', {selector: '.mode'})).toBeVisible()
  expect(screen.getByRole('button', {name: 'Load playback'})).toBeEnabled()
  // The transport deliberately ignores abort so even an already arriving result
  // cannot change the view or display an obsolete request error.
  await act(async () => {
    if (outcome === 'response') resolveResponse(json(saved))
    else rejectResponse(new Error('late playback failure'))
    await delayed.catch(() => undefined)
  })
  expect(screen.getByText('LIVE', {selector: '.mode'})).toBeVisible()
  expect(screen.queryByRole('button', {name: 'Return to live'})).not.toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})
