import { useLayoutEffect, useRef, useState } from 'react'
import type { Snapshot, TimelineEvent } from '../types'

function faultIntervals(events: TimelineEvent[]) {
  const intervals = new Map<string, {start: number; end: number | null; label: string}>()
  for (const event of events) {
    if (event.event_kind !== 'FaultActivated' && event.event_kind !== 'FaultCleared') continue
    const start = event.start_at_ns ?? event.sim_time_ns
    const key = `${event.fault_id}-${start}`
    intervals.set(key, {start, end: event.event_kind === 'FaultCleared' ? event.sim_time_ns : null,
      label: event.details})
  }
  return intervals
}

export function AnomalyChart({view}: {view: Snapshot}) {
  const chart = useRef<SVGSVGElement>(null)
  const [width, setWidth] = useState(600)
  const anomaly = view.anomaly
  useLayoutEffect(() => {
    if (!chart.current || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(entries => setWidth(Math.max(280, entries[0].contentRect.width)))
    observer.observe(chart.current)
    return () => observer.disconnect()
  }, [!!anomaly])

  if (!anomaly) return <section className="chart-panel" aria-label="Anomaly detection">
    <div className="section-heading"><h2>Anomaly detection</h2></div>
    <p className="chart-caption">No model is attached to this run. Start Studio with a trusted M9 model to score new network runs.</p>
  </section>

  const {model, scores, alerts} = anomaly
  const latest = scores.at(-1)
  const end = Math.max(1e9, view.sim_time_ns)
  const start = Math.max(0, end - 20e9)
  const x = (time: number) => 94 + (Math.max(start, Math.min(end, time)) - start) / (end - start) * (width - 112)
  const y = (score: number) => 118 - score * 100
  const inView = (from: number, to: number | null) => from <= end && (to ?? view.sim_time_ns) >= start
  const points = scores.filter(sample => sample.sim_time_ns >= start)
  const faults = faultIntervals([...(view.active_fault_markers ?? []), ...(view.timeline ?? [])])
  const format = (value: number | null) => value == null ? '—' : value.toFixed(2)

  return <section className="chart-panel anomaly-panel" aria-label="Anomaly detection">
    <div className="section-heading">
      <h2>Anomaly detection</h2>
      <span className="legend" title={model.model_sha256}>Model {model.model_id} · schema {model.feature_schema_version}</span>
    </div>
    <div className="anomaly-summary">
      <span>Score <strong data-testid="anomaly-score">{latest ? latest.score.toFixed(3) : '—'}</strong></span>
      <span>Threshold <strong data-testid="anomaly-threshold">{model.threshold.toFixed(3)}</strong></span>
      <span>{latest ? latest.eligible ? view.mode === 'live' ? 'Scoring active' : 'Recorded score' : 'Warm-up'
        : view.mode === 'live' ? 'Waiting for the first window' : 'No score at this playback position'}</span>
    </div>
    <svg ref={chart} viewBox={`0 0 ${width} 197`} role="img"
      aria-label="Anomaly score and threshold with separate model alert and injected fault tracks">
      {[0, 0.5, 1].map(score => <g key={score}>
        <line x1={x(start)} x2={x(end)} y1={y(score)} y2={y(score)} className="grid-line" />
        <text x={x(start) - 8} y={y(score) + 3} textAnchor="end">{score}</text>
      </g>)}
      <line x1={x(start)} x2={x(end)} y1={y(model.threshold)} y2={y(model.threshold)} className="anomaly-threshold" />
      {points.length > 0 && <polyline className="signal-line" points={points.map(point =>
        `${x(point.sim_time_ns)},${y(point.score)}`).join(' ')} />}
      {latest && <circle cx={x(latest.sim_time_ns)} cy={y(latest.score)} r="3" className="anomaly-point" />}
      <text x="4" y="141">Model alerts</text>
      <text x="4" y="162">Injected faults</text>
      {[136, 157].map(at => <line key={at} x1={x(start)} x2={x(end)} y1={at} y2={at} className="grid-line" />)}
      {alerts.filter(episode => inView(episode.start_ns, episode.end_ns)).map(episode =>
        <rect key={episode.start_ns} data-testid="anomaly-episode" className="anomaly-episode"
          x={x(episode.start_ns)} y="131" height="10" rx="2"
          width={Math.max(2, x(episode.end_ns ?? view.sim_time_ns) - x(episode.start_ns))}>
          <title>Model alert · peak {episode.peak_score.toFixed(3)}</title>
        </rect>)}
      {Array.from(faults).filter(([, fault]) => inView(fault.start, fault.end)).map(([key, fault]) =>
        <rect key={key} data-testid="ground-truth-fault" className="anomaly-fault"
          x={x(fault.start)} y="152" height="10" rx="2"
          width={Math.max(2, x(fault.end ?? view.sim_time_ns) - x(fault.start))}>
          <title>{fault.label}</title>
        </rect>)}
      {[start, (start + end) / 2, end].map(time => <text key={time} x={x(time)} y="185" textAnchor="middle">
        {(time / 1e9).toFixed(2)} s</text>)}
    </svg>
    <div className="chart-caption anomaly-caption">
      <span>{alerts.length} recent model alert {alerts.length === 1 ? 'episode' : 'episodes'}. An episode closes after two windows below threshold.</span>
      {latest && <span>Latest CAN observations · Timed out IDs {latest.timed_out_can_ids.length ?
        latest.timed_out_can_ids.map(id => `0x${id.toString(16)}`).join(', ') : 'none'} · RPM change {format(latest.rpm_derivative_max)} rpm/s · Wheel speed residual {format(latest.wheel_residual_max)} km/h</span>}
    </div>
  </section>
}
