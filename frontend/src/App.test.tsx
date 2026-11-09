import { act, render, screen, waitFor, within } from '@testing-library/react'
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

test('live controls become ready while the saved recording catalog is pending', async () => {
  let resolveRuns!: (response: Response) => void
  const pendingRuns = new Promise<Response>(resolve => {resolveRuns = resolve})
  vi.stubGlobal('fetch', async (path: string) => {
    if (path === '/api/simulations/current') return Response.json(null)
    if (path === '/api/runs') return pendingRuns
    if (path === '/api/scenarios') return Response.json([])
    if (path === '/api/faults') return Response.json({types: [], messages: []})
    throw new Error(`Unexpected request: ${path}`)
  })
  render(<App />)
  await waitFor(() => expect(screen.getByRole('button', {name: 'Run demo'})).toBeEnabled())
  resolveRuns(Response.json([{id: saved.id, duration_ns: saved.duration_ns}]))
  expect(await screen.findByRole('option', {name: 'saved-ru · 1.00 s'})).toBeInTheDocument()
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

const guidedScenario = {
  id: 'abs_outage_walkthrough', name: 'ABS signal loss', description: 'Loss and recovery.',
  duration_ns: 12e9, seed: 42, profile_id: 'democar-network', source_sha256: 'a'.repeat(64),
}
const guided: Snapshot = {
  ...live, id: 'guided-run', scenario: guidedScenario,
  state: {...live.state, profile_id: 'democar-network', kombi_speed_status: 'UNAVAILABLE'},
}

test('guided demo starts and opens recorded checkpoints even while the catalog is pending', async () => {
  let started = false
  vi.stubGlobal('fetch', async (path: string, options?: RequestInit) => {
    if (path === '/api/simulations/current') return Response.json(null)
    if (path === '/api/runs') return new Promise<Response>(() => {})
    if (path === '/api/scenarios') return Response.json([guidedScenario])
    if (path === '/api/faults') return Response.json({types: [], messages: []})
    if (path === '/api/simulations' && options?.method === 'POST') {
      expect(JSON.parse(String(options.body))).toEqual({scenario_id: guidedScenario.id, seed: 42})
      return Response.json(guided)
    }
    if (path === '/api/simulations/guided-run/start') {
      started = true
      return Response.json(guided)
    }
    const time = new Map([
      ['/api/runs/guided-run/playback?until_ns=2000000000', 2e9],
      ['/api/runs/guided-run/playback?until_ns=6000000000', 6e9],
      ['/api/runs/guided-run/playback?until_ns=10000000000', 10e9],
    ]).get(path)
    if (time !== undefined) return Response.json({
      ...guided, mode: 'playback', stopped: true, recording: 'saved', duration_ns: 12e9,
      sim_time_ns: time,
      state: {...guided.state, kombi_status: 'FRESH', kombi_rpm: 1800,
        kombi_speed_kph: 20, kombi_speed_status: time === 6e9 ? 'TIMEOUT' : 'FRESH'},
    })
    throw new Error(`Unexpected request: ${path}`)
  })
  const user = userEvent.setup()
  render(<App />)
  const guide = within(await screen.findByRole('region', {name: 'Guided demo'}))
  await user.click(guide.getByRole('button', {name: /Run guided demo/}))
  await waitFor(() => expect(started).toBe(true))
  expect(guide.getByRole('button', {name: /Run guided demo/})).toBeDisabled()
  act(() => useStudio.getState().adopt({
    ...guided, stopped: true, recording: 'saved', sim_time_ns: 12e9,
    state: {...guided.state, kombi_speed_status: 'FRESH', kombi_status: 'FRESH'},
    timeline: [{event_kind: 'EcuStatusChanged', event_seq: 20, sim_time_ns: 4.3e9,
      title: 'KOMBI TIMEOUT', details: 'WheelSpeeds'}],
  }))
  expect(guide.getByText(/Reception restored/)).toBeVisible()
  await user.click(guide.getByRole('button', {name: /Inspect outage/}))
  expect(await guide.findByText('TIMEOUT')).toBeVisible()
  expect(screen.getByText('PLAYBACK', {selector: '.mode'})).toBeVisible()
  expect(screen.getByRole('combobox', {name: 'Saved recording'})).toHaveValue('guided-run')
  expect(screen.getByRole('button', {name: 'Deterministic rerun'})).toBeEnabled()
  await user.click(guide.getByRole('button', {name: /Before outage/}))
  await waitFor(() => expect(useStudio.getState().playback?.sim_time_ns).toBe(2e9))
  await user.click(guide.getByRole('button', {name: /After recovery/}))
  await waitFor(() => expect(useStudio.getState().playback?.sim_time_ns).toBe(10e9))
  await user.click(guide.getByRole('button', {name: 'Back to live controls'}))
  expect(screen.getByText('LIVE', {selector: '.mode'})).toBeVisible()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

test.each(['saved', 'failed'] as const)('an interrupted guided recording (%s) never claims completion', async recording => {
  const partial = {...guided, sim_time_ns: 1e9, stopped: true, recording}
  vi.stubGlobal('fetch', async (path: string) => {
    if (path === '/api/simulations/current') return Response.json(partial)
    if (path === '/api/runs') return Response.json([])
    if (path === '/api/scenarios') return Response.json([guidedScenario])
    if (path === '/api/faults') return Response.json({types: [], messages: []})
    throw new Error(`Unexpected request: ${path}`)
  })
  render(<App />)
  const guide = within(await screen.findByRole('region', {name: 'Guided demo'}))
  await waitFor(() => expect(useStudio.getState().live?.id).toBe('guided-run'))
  expect(guide.queryByText(/Reception restored/)).not.toBeInTheDocument()
  expect(guide.getByRole('button', {name: /Inspect outage/})).toBeDisabled()
  expect(guide.getByText(recording === 'saved' ? /Stopped early/ : /Recording failed/)).toBeVisible()
})
