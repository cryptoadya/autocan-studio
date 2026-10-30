import { render, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { useLiveSocket } from './api'
import { useStudio } from './store'

class Socket {
  static latest: Socket
  onclose: (() => void) | null = null
  onmessage = null
  onerror = null
  constructor() {Socket.latest = this}
  close() {this.onclose?.()}
}
function LiveConnection() {useLiveSocket('old-run'); return null}

afterEach(() => {vi.unstubAllGlobals(); useStudio.getState().clear()})

test('server restart reconciles a missing active run instead of retrying an obsolete ID', async () => {
  vi.stubGlobal('WebSocket', Socket)
  vi.stubGlobal('fetch', async () => new Response('null', {status: 200}))
  useStudio.setState({live: {
    id: 'old-run', mode: 'live', sim_time_ns: 0, frames: [], signals: [],
    stopped: false, recording: 'recording', error: null,
    state: {sim_time_ns: 0, paused: true, throttle_percent: 0, kombi_rpm: null,
      kombi_status: 'UNAVAILABLE', engine_running: true, engine_publishing: true,
      last_received_ns: null, frames_published: 0, deliveries: 0},
  }, connected: true})
  render(<LiveConnection />)
  Socket.latest.close()
  await waitFor(() => expect(useStudio.getState().live).toBeNull())
  expect(useStudio.getState().connected).toBe(false)
})
