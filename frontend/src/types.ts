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
  active_fault_count?: number
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
  created_at_ns?: number
}
export interface Signal { sim_time_ns: number; rpm: number | null; status: Freshness }
export interface AnomalyView {
  model: {model_id: string; model_sha256: string; threshold: number; feature_schema_version: number}
  scores: {
    sim_time_ns: number; score: number; eligible: boolean; timed_out_can_ids: number[]
    rpm_derivative_max: number | null; wheel_residual_max: number | null
  }[]
  alerts: {start_ns: number; end_ns: number | null; peak_score: number}[]
}
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
  scenario?: DemoScenario | null
  active_faults?: ActiveFault[]
  timeline?: TimelineEvent[]
  timeline_truncated?: number
  anomaly?: AnomalyView | null
}
export interface WireMessage extends Snapshot {
  type: 'snapshot' | 'frames_batch'
  schema_version: number
  seq: number
  skipped_frames: number
}
export interface SavedRun { id: string; duration_ns: number }
export interface DemoScenario {
  id: string; name: string; description: string; duration_ns: number
  profile_id: string; seed: number; source_sha256: string
}
export interface FaultSpec {
  fault_id: string; type: string; target_ecu: string; target_message_id: number | null
  signal?: string | null; parameters: Record<string, number>; seed: number
}
export interface ActiveFault {fault: FaultSpec; start_at_ns: number}
export interface TimelineEvent {
  event_seq: number; sim_time_ns: number; event_kind: string; title: string; details: string
  fault_id?: string; start_at_ns?: number
}
export interface FaultParameter {
  name: string; label: string; minimum: number; maximum: number; step: number
  default: number | null; scale: number; optional: boolean
}
export interface FaultType {
  id: string; name: string; requires_signal: boolean; wheel_only: boolean; parameters: FaultParameter[]
}
export interface FaultSignal {name: string; minimum: number; maximum: number; step: number; unit: string}
export interface FaultMessage {can_id: number; name: string; ecu_id: string; signals: FaultSignal[]}
export interface FaultCatalog {types: FaultType[]; messages: FaultMessage[]}
