export type Freshness = 'UNAVAILABLE' | 'FRESH' | 'STALE' | 'TIMEOUT'
export type IgnitionRequest = 'OFF' | 'ACC' | 'ON' | 'START'
export type Gear = 'P' | 'R' | 'N' | 'D'
export interface SimState {
  profile_id?: string
  sim_time_ns: number
  paused: boolean
  throttle_percent: number
  brake_percent?: number
  gear?: Gear
  shift_rejection?: 'vehicle_moving' | 'brake_required' | 'inputs_unavailable' | null
  abs_publishing?: boolean
  transmission_publishing?: boolean
  body_publishing?: boolean
  kombi_publishing?: boolean
  kombi_speed_kph?: number | null
  kombi_speed_status?: Freshness
  kombi_temperature_c?: number | null
  kombi_temperature_status?: Freshness
  kombi_engine_on?: boolean | null
  kombi_gear?: Gear | null
  kombi_gear_status?: Freshness
  kombi_ignition?: string | null
  kombi_body_status?: Freshness
  kombi_wheels_consistent?: boolean | null
  kombi_abs_status?: Freshness
  kombi_heartbeat_status?: Freshness
  physical_speed_kph?: number
  kombi_rpm: number | null
  kombi_status: Freshness
  engine_running: boolean
  ignition_state?: Exclude<IgnitionRequest, 'START'>
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
  rpm: number | null
  message_name?: string
  decoded?: string
  display_seq?: number
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
