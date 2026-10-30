import type { Freshness, SimState } from '../types'

const sources = [
  ['abs', 'ABS/DSC', 'abs_publishing', 'kombi_speed_status'],
  ['transmission', 'Transmission', 'transmission_publishing', 'kombi_gear_status'],
  ['body', 'Body', 'body_publishing', 'kombi_body_status'],
  ['KOMBI', 'KOMBI', 'kombi_publishing', 'kombi_heartbeat_status'],
] as const

function Status({value}: {value: Freshness | undefined}) {
  return <span className={`network-status ${(value ?? 'UNAVAILABLE').toLowerCase()}`}>{value ?? 'UNAVAILABLE'}</span>
}

export function NetworkPanel({state, disabled, onOffline}: {
  state: SimState; disabled: boolean; onOffline: (ecu: string, offline: boolean) => void
}) {
  return <section className="network-panel" aria-label="ECU network">
    <h2>KOMBI <span>Received CAN measurements · DemoCar 0.2</span></h2>
    <div className="network-readings">
      <div><div className="panel-label">Vehicle speed</div><output data-testid="can-speed">{state.kombi_speed_kph?.toFixed(1) ?? '—'} <small>km/h</small></output><Status value={state.kombi_speed_status} /></div>
      <div><div className="panel-label">Coolant temperature</div><output data-testid="can-temperature">{state.kombi_temperature_c?.toFixed(1) ?? '—'} <small>°C</small></output><Status value={state.kombi_temperature_status} /></div>
      <div><div className="panel-label">Transmission</div><output data-testid="can-gear">{state.kombi_gear ?? '—'}</output><Status value={state.kombi_gear_status} /></div>
      <div><div className="panel-label">Body ignition</div><output data-testid="can-ignition">{state.kombi_ignition ?? '—'}</output><Status value={state.kombi_body_status} /></div>
      <div><div className="panel-label">Wheel consistency</div><output data-testid="can-wheels">{state.kombi_wheels_consistent == null ? '—' : state.kombi_wheels_consistent ? 'OK' : 'Mismatch'}</output><Status value={state.kombi_abs_status} /></div>
    </div>
    <p className="help">Each measurement keeps its last received value when stale or timed out. Wheel speeds use a straight-line model with no slip.</p>
    <div className="network-sources">{sources.map(([id, label, publishing, freshness]) => {
      const online = state[publishing] ?? true
      return <div className="network-source" key={id}>
        <div><strong>{label}</strong><Status value={state[freshness]} /></div>
        <button className={online ? 'secondary' : 'quiet'} disabled={disabled}
          aria-label={`${online ? 'Stop' : 'Resume'} ${label} publication`}
          onClick={() => onOffline(id, online)}>{online ? 'Stop publication' : 'Resume publication'}</button>
      </div>
    })}</div>
    <p className="help">Publication controls suppress outgoing messages; reception continues. Engine publication is controlled above. KOMBI heartbeat is observed independently.</p>
  </section>
}
