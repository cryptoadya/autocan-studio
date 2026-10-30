export type Freshness = 'UNAVAILABLE' | 'FRESH' | 'STALE' | 'TIMEOUT'
export interface SimState {
  sim_time_ns: number
  paused: boolean
  throttle_percent: number
  kombi_rpm: number | null
  kombi_status: Freshness
  engine_running: boolean
  engine_publishing: boolean
  last_received_ns: number | null
  frames_published: number
  deliveries: number
}
export interface Frame {
  frame_id: number
  event_seq: number
  sim_time_ns: number
  can_id: number
  dlc: number
  payload_hex: string
  rpm: number
}
export interface Signal { sim_time_ns: number; rpm: number | null; status: Freshness }
export interface Snapshot {
  id: string
  mode: 'live' | 'playback'
  sim_time_ns: number
  state: SimState
  frames: Frame[]
  signals: Signal[]
  stopped: boolean
  recording: 'recording' | 'saved' | 'failed'
  error: string | null
  duration_ns?: number
}
export interface WireMessage extends Snapshot {
  type: 'snapshot' | 'frames_batch'
  schema_version: number
  seq: number
  skipped_frames: number
}
export interface SavedRun { id: string; duration_ns: number }
