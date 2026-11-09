import type { DemoScenario, Snapshot } from '../types'

const DEMO_ID = 'abs_outage_walkthrough'
// These checkpoints belong to the packaged 12-second scenario, not to arbitrary runs.
const stages = [
  {start: 0, end: 4e9, seek: 2e9, title: 'Normal reception', action: 'Before outage',
    description: 'The car accelerates. Wheel speed and engine RPM arrive over CAN.'},
  {start: 4e9, end: 8e9, seek: 6e9, title: 'ABS goes silent', action: 'Inspect outage',
    description: 'Wheel speed becomes STALE, then TIMEOUT. Engine messages keep arriving.'},
  {start: 8e9, end: 12e9, seek: 10e9, title: 'Reception recovers', action: 'After recovery',
    description: 'ABS resumes sending. The cluster receives fresh wheel speeds again.'},
]

export function GuidedDemo({scenarios, view, busy, runDisabled, onRun, onSeek, onLive}: {
  scenarios: DemoScenario[]
  view: Snapshot | null
  busy: boolean
  runDisabled: boolean
  onRun: (id: string, seed: number) => void
  onSeek: (id: string, time: number) => void
  onLive: () => void
}) {
  const run = view?.scenario?.id === DEMO_ID ? view : null
  const scenario = run?.scenario ?? scenarios.find(item => item.id === DEMO_ID)
  if (!scenario) return null

  const time = run?.sim_time_ns ?? 0
  const playback = run?.mode === 'playback'
  const saved = run?.recording === 'saved'
  const recordedUntil = playback ? run.duration_ns ?? time : run?.stopped ? time : 0
  const complete = saved && recordedUntil >= scenario.duration_ns
  const timedOut = run?.timeline?.some(event => event.event_kind === 'EcuStatusChanged'
    && event.details === 'WheelSpeeds' && event.title === 'KOMBI TIMEOUT')
  const speedStatus = run?.state.kombi_speed_status ?? 'UNAVAILABLE'
  const rpmStatus = run?.state.kombi_status ?? 'UNAVAILABLE'
  const recovered = complete && timedOut && speedStatus === 'FRESH' && time >= 8e9
  const stage = stages.findIndex(item => time < item.end)
  const currentStage = stage < 0 ? stages.length - 1 : stage
  const heading = playback ? 'Inspecting the saved recording'
    : run?.recording === 'failed' ? 'Recording failed — this run cannot be replayed'
    : run?.stopped ? complete
      ? recovered ? 'Signal loss captured. Reception restored.' : 'Recording saved — inspect the observations'
      : 'Stopped early — partial recording saved'
    : run?.state.paused ? 'Demo paused — resume with the controls below'
    : 'Watch the received signals'

  return <section className="guided-demo" aria-label="Guided demo">
    <div className="guided-heading">
      <div>
        <p className="guided-eyebrow">START HERE · 12-SECOND GUIDED DEMO</p>
        <h2>What happens when ABS stops sending?</h2>
        <p>The instrument cluster loses wheel-speed updates while the engine keeps reporting.</p>
      </div>
      <button className="primary" disabled={runDisabled}
        onClick={() => onRun(scenario.id, scenario.seed)}>Run guided demo · 12 s</button>
    </div>
    <ol className="guided-stages" aria-label="Scripted sequence in virtual time">
      {stages.map((item, index) => <li key={item.title}
        aria-current={run && currentStage === index ? 'step' : undefined}>
        <span className="guided-stage-time">{item.start / 1e9}–{item.end / 1e9} s · {index + 1}/3</span>
        <h3>{item.title}</h3><p>{item.description}</p>
      </li>)}
    </ol>
    {run ? <>
      <div className="guided-result">
        <h3 role="status">{heading}</h3>
        <p>{playback ? 'Recorded' : 'Live'} CAN observations at {(time / 1e9).toFixed(2)} s
          {saved && !playback ? ' · Recording saved' : ''}</p>
      </div>
      <div className="guided-readings">
        <div aria-label="Guided wheel speed">
          <span>Wheel speed received by cluster</span>
          <strong>{run.state.kombi_speed_kph == null ? '—' : run.state.kombi_speed_kph.toFixed(1)} <small>km/h</small></strong>
          <span className={`network-status ${speedStatus.toLowerCase()}`}>{speedStatus}</span>
        </div>
        <div aria-label="Guided engine RPM">
          <span>Engine RPM received by cluster</span>
          <strong>{run.state.kombi_rpm == null ? '—' : Math.round(run.state.kombi_rpm).toLocaleString()} <small>rpm</small></strong>
          <span className={`network-status ${rpmStatus.toLowerCase()}`}>{rpmStatus}</span>
        </div>
      </div>
      <p className="guided-note">STALE / TIMEOUT keeps the last received value; it is not a fresh measurement.
        {run.state.paused && !run.stopped ? ' Virtual time is paused.' : ''}</p>
      <div className="guided-playback" aria-label="Guided playback checkpoints">
        {stages.map((item, index) => <button key={item.action}
          className={index === 1 ? 'primary' : 'secondary'}
          disabled={busy || !saved || item.seek > recordedUntil}
          aria-pressed={playback && time === item.seek}
          onClick={() => onSeek(run.id, item.seek)}>{item.action} · {item.seek / 1e9} s</button>)}
        {saved && !complete && <button className="secondary" disabled={busy}
          onClick={() => onSeek(run.id, recordedUntil)}>Open partial recording</button>}
        {playback && <button className="quiet" onClick={onLive}>Back to live controls</button>}
        {!saved && <span>Playback becomes available when the recording is saved.</span>}
      </div>
    </> : <p className="guided-note">No model or hardware needed. The run saves automatically;
      then compare the recording before, during and after the outage.
      {runDisabled ? ' Finish the current run and return to live controls to start.' : ''}</p>}
  </section>
}
