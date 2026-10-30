import { beforeEach, expect, test } from 'vitest'
import { useStudio } from './store'
import type { Frame, WireMessage } from './types'

export function message(overrides: Partial<WireMessage> = {}): WireMessage {
  return {
    id: 'run-one', type: 'snapshot', schema_version: 1, seq: 1,
    mode: 'live', sim_time_ns: 0, stopped: false, recording: 'recording', error: null,
    skipped_frames: 0, frames: [], signals: [],
    state: {
      sim_time_ns: 0, paused: true, throttle_percent: 0,
      kombi_rpm: null, kombi_status: 'UNAVAILABLE', engine_running: true,
      engine_publishing: true, last_received_ns: null, frames_published: 0, deliveries: 0,
    },
    ...overrides,
  }
}
const frame = (id: number): Frame => ({ frame_id: id, event_seq: id * 2,
  sim_time_ns: id * 20_000_000, can_id: 512, dlc: 2, payload_hex: '800c', rpm: 800 })

beforeEach(() => useStudio.getState().clear())

test('live deltas cap monitor and plot; reconnect snapshot replaces old history', () => {
  useStudio.getState().ingest(message())
  for (let seq = 2; seq <= 502; seq++) {
    useStudio.getState().ingest(message({type: 'frames_batch', seq,
      frames: [frame(seq)], signals: [{sim_time_ns: seq, rpm: 800, status: 'FRESH'}]}))
  }
  expect(useStudio.getState().live?.frames).toHaveLength(200)
  expect(useStudio.getState().live?.signals).toHaveLength(400)
  useStudio.getState().ingest(message({frames: [frame(900)]}))
  expect(useStudio.getState().live?.frames.map(f => f.frame_id)).toEqual([900])
})

test('missing sequence rejects a delta until a fresh snapshot arrives', () => {
  useStudio.getState().ingest(message({frames: [frame(1)]}))
  expect(useStudio.getState().ingest(message({type: 'frames_batch', seq: 3,
    frames: [frame(3)]}))).toBe(false)
  expect(useStudio.getState().live?.frames.map(f => f.frame_id)).toEqual([1])
  expect(useStudio.getState().ingest(message({frames: [frame(4)]}))).toBe(true)
  expect(useStudio.getState().live?.frames.map(f => f.frame_id)).toEqual([4])
})

test('a new run snapshot discards old frames; playback is isolated from live deltas', () => {
  useStudio.getState().ingest(message({frames: [frame(1)]}))
  useStudio.getState().setPlayback({...message(), mode: 'playback', id: 'saved'})
  useStudio.getState().ingest(message({id: 'run-two'}))
  expect(useStudio.getState().live?.frames).toEqual([])
  expect(useStudio.getState().playback?.id).toBe('saved')
})
