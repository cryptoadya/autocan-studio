import { useEffect, useState } from 'react'
import type { Gear, SimState } from '../types'

interface Props {
  state: SimState | undefined
  disabled: boolean
  onBrake: (value: number) => void
  onGear: (value: Gear) => void
}

export function MotionControls({state, disabled, onBrake, onGear}: Props) {
  const [brake, setBrake] = useState(state?.brake_percent ?? 0)
  const [editing, setEditing] = useState(false)
  useEffect(() => {
    if (!editing) setBrake(state?.brake_percent ?? 0)
  }, [state?.brake_percent, editing])
  const commit = () => {setEditing(false); onBrake(brake)}
  const rejection = state?.shift_rejection === 'vehicle_moving'
    ? 'Stop the vehicle before selecting P, R or D.'
    : state?.shift_rejection === 'brake_required'
      ? 'Press the brake before selecting P, R or D.' : ''
  return <section className="motion-controls" aria-label="Motion · physical model">
    <h2>Motion <span>Physical model</span></h2>
    <div className="motion-grid">
      <div><div className="panel-label">Physical model speed</div>
        <div className="motion-speed" data-testid="physical-speed">
          {state?.physical_speed_kph == null ? '—' : state.physical_speed_kph.toFixed(1)} <small>km/h</small>
        </div>
        <p className="help">Model telemetry, not a CAN measurement. Negative values mean reverse.</p>
      </div>
      <div><label className="brake-label" htmlFor="brake">Brake <span>{brake}%</span></label>
        <input id="brake" aria-label="Brake" type="range" min="0" max="100" step="1" value={brake}
          disabled={disabled} onPointerDown={() => setEditing(true)}
          onChange={e => {setEditing(true); setBrake(Number(e.target.value))}}
          onPointerUp={commit} onKeyUp={commit} onBlur={() => {if (editing) commit()}} />
        <p className="help">Release to apply. Full brake holds against full throttle.</p>
      </div>
      <div><label className="gear-label" htmlFor="gear">Gear</label>
        <select id="gear" aria-label="Gear" value={state?.gear ?? 'P'} disabled={disabled}
          onChange={e => onGear(e.target.value as Gear)}>
          <option value="P">P · Park</option><option value="R">R · Reverse</option>
          <option value="N">N · Neutral</option><option value="D">D · Drive</option>
        </select>
        <p className="help">Select P/R/D while stopped with at least 10% brake. N coasts.</p>
        {rejection && <p className="shift-rejection" role="status">Shift rejected: {rejection}</p>}
      </div>
    </div>
  </section>
}
