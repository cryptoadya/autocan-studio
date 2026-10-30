import { useEffect, useState } from 'react'
import type { IgnitionRequest, SimState } from '../types'

interface Props {
  state: SimState | undefined
  disabled: boolean
  onThrottle: (value: number) => void
  onOffline: (offline: boolean) => void
  onIgnition: (value: IgnitionRequest) => void
  onEngineRunning: (running: boolean) => void
}

export function Dashboard({state, disabled, onThrottle, onOffline, onIgnition, onEngineRunning}: Props) {
  const [throttle, setThrottle] = useState(state?.throttle_percent ?? 0)
  const [editing, setEditing] = useState(false)
  useEffect(() => {
    if (!editing) setThrottle(state?.throttle_percent ?? 0)
  }, [state?.throttle_percent, editing])
  const status = state?.kombi_status ?? 'UNAVAILABLE'
  const commit = () => { setEditing(false); onThrottle(throttle) }
  return <section className="dashboard" aria-label="Vehicle dashboard">
    <div className="rpm-panel">
      <div className="panel-label">KOMBI / Engine speed</div>
      <div className="rpm-value" data-testid="received-rpm">
        {state?.kombi_rpm == null ? '—' : state.kombi_rpm.toLocaleString('en-US', {maximumFractionDigits: 0})}
        <span>rpm</span>
      </div>
      <div className="rpm-track"><i style={{width: `${(state?.kombi_rpm ?? 0) / 80}%`}} /></div>
      <div className="rpm-scale"><span>0</span><span>4,000</span><span>8,000</span></div>
      <div className="measurement"><strong className={`status ${status.toLowerCase()}`}>{status}</strong>
        <span>{status === 'FRESH' ? 'Received over CAN' : status === 'UNAVAILABLE'
          ? 'Waiting for the first CAN delivery' : 'Last received measurement; not current RPM'}</span></div>
    </div>
    <div className="driver-panel">
      <div className="panel-label">Driver input</div>
      <label className="throttle-label" htmlFor="throttle">Throttle <output>{throttle}<small>%</small></output></label>
      <input id="throttle" aria-label="Throttle" type="range" min="0" max="100" step="1" value={throttle}
        disabled={disabled} onPointerDown={() => setEditing(true)}
        onChange={event => {setEditing(true); setThrottle(Number(event.target.value))}}
        onPointerUp={commit} onKeyUp={commit} onBlur={() => {if (editing) commit()}} />
      <div className="input-scale"><span>Idle</span><span>Full throttle</span></div>
      <p className="help">Release to apply. Commands use the next simulation tick.</p>
      <div className="physical-engine-controls">
        <p>Physical engine: {state ? state.engine_running ? 'Running' : 'Stopped' : '—'}</p>
        <label htmlFor="ignition">Ignition</label>
        <div className="engine-actions">
          <select id="ignition" aria-label="Ignition" value={state?.ignition_state ?? 'ON'}
            disabled={disabled} onChange={e => onIgnition(e.target.value as IgnitionRequest)}>
            <option value="OFF">OFF</option><option value="ACC">ACC</option>
            <option value="ON">ON</option><option value="START">START (momentary)</option>
          </select>
          <button className="secondary" disabled={disabled}
            onClick={() => onEngineRunning(state?.engine_running === false)}>
            {state?.engine_running === false ? 'Start engine' : 'Stop engine'}
          </button>
        </div>
      </div>
    </div>
    <div className="ecu-panel">
      <div className="panel-label">Engine ECU / Publication</div>
      <div className="ecu-state"><span className={`indicator ${state?.engine_publishing === false ? 'off' : ''}`} />
        {state?.engine_publishing === false ? 'Offline' : 'Online'}</div>
      <p>Offline stops CAN publication.<br />Physical engine controls are independent.</p>
      <button className={state?.engine_publishing === false ? 'secondary' : 'danger-outline'}
        disabled={disabled} onClick={() => onOffline(state?.engine_publishing !== false)}
        aria-label={state?.engine_publishing === false ? 'Bring Engine ECU online' : 'Take Engine ECU offline'}>
        {state?.engine_publishing === false ? 'Bring online' : 'Take offline'}</button>
    </div>
  </section>
}
