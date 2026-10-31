import { useState } from 'react'
import type { FaultCatalog, FaultSpec, FaultType, Snapshot } from '../types'

interface Props {
  catalog: FaultCatalog | null
  view: Snapshot | null
  disabled: boolean
  onInject: (fault: FaultSpec) => void
  onClear: (id: string) => void
  onSeek: (time: number) => void
}

function faultDescription(fault: FaultSpec, catalog: FaultCatalog | null) {
  if (fault.type === 'ecu_offline') return 'Publication suppressed'
  if (fault.type === 'signal_stuck' && !('value' in fault.parameters)) return 'Captured at first publication'
  const fields = catalog?.types.find(type => type.id === fault.type)?.parameters ?? []
  return Object.entries(fault.parameters).map(([name, value]) => {
    const parameter = fields.find(field => field.name === name)
    return `${parameter?.label ?? name.replaceAll('_', ' ')}: ${value / (parameter?.scale ?? 1)}`
  }).join(' · ')
}

function FaultEditor({catalog, kind, disabled, onType, onInject}: {
  catalog: FaultCatalog; kind: FaultType; disabled: boolean
  onType: (id: string) => void; onInject: (fault: FaultSpec) => void
}) {
  const [identifier, setIdentifier] = useState('manual-1')
  const [seed, setSeed] = useState('0')
  const [messageId, setMessageId] = useState('512')
  const [ecu, setEcu] = useState('engine')
  const [signalName, setSignalName] = useState('EngineRpm')
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(
    kind.parameters.map(p => [p.name, p.default === null ? '' : String(p.default)]),
  ))
  const choices = catalog.messages.filter(m => (!kind.requires_signal || m.signals.length > 0) &&
    (!kind.wheel_only || m.name === 'WheelSpeeds'))
  const message = choices.find(m => String(m.can_id) === messageId) ?? choices[0]
  const signal = message?.signals.find(s => s.name === signalName) ?? message?.signals[0]
  const wholeEcu = kind.id === 'ecu_offline' && messageId === 'all'
  return <form className="fault-editor" onSubmit={event => {
    event.preventDefault()
    if (disabled || !message) return
    const parameters = Object.fromEntries(kind.parameters.flatMap(p => {
      const raw = values[p.name] ?? ''
      return p.optional && raw === '' ? [] : [[p.name, Number(raw) * p.scale]]
    }))
    onInject({fault_id: identifier, type: kind.id, target_ecu: wholeEcu ? ecu : message.ecu_id,
      target_message_id: wholeEcu ? null : message.can_id, parameters, seed: Number(seed),
      ...(kind.requires_signal && signal ? {signal: signal.name} : {})})
  }}>
    <fieldset disabled={disabled}>
      <legend>Inject a synthetic fault</legend>
      <div className="fault-fields">
        <label>Fault type<select value={kind.id} onChange={e => onType(e.target.value)}>
          {catalog.types.map(type => <option key={type.id} value={type.id}>{type.name}</option>)}
        </select></label>
        <label>Target message<select value={wholeEcu ? 'all' : message?.can_id ?? ''} onChange={e => {
          setMessageId(e.target.value)
          if (kind.id === 'signal_spike') {
            const next = choices.find(m => String(m.can_id) === e.target.value)?.signals[0]
            if (next) setValues(previous => ({...previous, value: String(next.maximum)}))
          }
        }}>
          {kind.id === 'ecu_offline' && <option value="all">All messages from ECU</option>}
          {choices.map(m => <option key={m.can_id} value={m.can_id}>{m.ecu_id} · {m.name} · 0x{m.can_id.toString(16)}</option>)}
        </select></label>
        {wholeEcu && <label>Target ECU<select value={ecu} onChange={e => setEcu(e.target.value)}>
          {['engine', 'abs', 'transmission', 'body', 'KOMBI'].map(id => <option key={id}>{id}</option>)}
        </select></label>}
        {kind.requires_signal && <label>Signal<select value={signal?.name ?? ''} onChange={e => {
          setSignalName(e.target.value)
          if (kind.id === 'signal_spike') {
            const next = message?.signals.find(s => s.name === e.target.value)
            if (next) setValues(previous => ({...previous, value: String(next.maximum)}))
          }
        }}>{message?.signals.map(s => <option key={s.name}>{s.name}</option>)}</select></label>}
        {kind.parameters.map(parameter => {
          const bounds = parameter.name === 'value' ? signal : undefined
          const label = `${parameter.label}${bounds?.unit ? ` (${bounds.unit})` : ''}`
          return <label key={parameter.name}>{label}<input type="number" value={values[parameter.name] ?? ''}
            min={bounds?.minimum ?? parameter.minimum} max={bounds?.maximum ?? parameter.maximum}
            step={bounds?.step ?? parameter.step} required={!parameter.optional}
            onChange={e => setValues(previous => ({...previous, [parameter.name]: e.target.value}))} /></label>
        })}
        <label>Fault ID<input value={identifier} maxLength={64} required onChange={e => setIdentifier(e.target.value)} /></label>
        <label>Fault seed<input type="number" value={seed} min="0" max="4294967295" step="1" required onChange={e => setSeed(e.target.value)} /></label>
      </div>
      <button className="secondary" type="submit">Inject fault</button>
    </fieldset>
  </form>
}

export function FaultLab({catalog, view, disabled, onInject, onClear, onSeek}: Props) {
  const [type, setType] = useState('signal_spike')
  const kind = catalog?.types.find(t => t.id === type) ?? catalog?.types[0]
  const active = view?.active_faults ?? []
  const timeline = view?.timeline ?? []
  const canSeek = view?.recording === 'saved'
  return <section className="fault-lab" aria-label="Scenarios and faults">
    <div className="fault-heading"><div><h2>Fault lab <span>Demo ground truth</span></h2>
      <p>Change transmitted signals, publication or delivery. Received CAN measurements remain separate.</p></div>
      <span className="fault-count">{active.length} active</span></div>
    {catalog && kind ? <FaultEditor key={kind.id} catalog={catalog} kind={kind} disabled={disabled}
      onType={setType} onInject={onInject} /> : <p className="empty-faults">Fault choices are loading.</p>}
    <div className="active-faults" aria-label="Active faults">
      <h3>{view?.mode === 'playback' ? 'Active at this playback position' : 'Active faults'}</h3>
      {active.length === 0 ? <p className="empty-faults">No active library faults.</p> : active.map(({fault, start_at_ns}) =>
        <div className="active-fault" key={fault.fault_id}><div><strong>{fault.fault_id}</strong>
          <span>{fault.type.replaceAll('_', ' ')} · {fault.target_ecu}{fault.signal ? ` / ${fault.signal}` : ''}</span>
          <small>{faultDescription(fault, catalog)} · since {(start_at_ns / 1e9).toFixed(3)} s</small></div>
          <button className="quiet" disabled={disabled} onClick={() => onClear(fault.fault_id)} aria-label={`Clear ${fault.fault_id}`}>Clear</button>
        </div>)}
    </div>
    <section className="fault-timeline" aria-label="Fault timeline">
      <div className="timeline-heading"><h3>Timeline</h3><span>Applied commands · fault episodes · KOMBI freshness</span></div>
      {(view?.timeline_truncated ?? 0) > 0 && <p className="empty-faults">{view?.timeline_truncated} earlier events omitted</p>}
      {timeline.length === 0 ? <p className="empty-faults">Events appear when virtual time advances. Commands queued while paused apply on resume.</p> :
        <ol>{timeline.slice().reverse().map(event => <li key={event.event_seq} data-kind={event.event_kind}>
          {canSeek ? <button className="timeline-time" onClick={() => onSeek(event.sim_time_ns)}
            aria-label={`Seek to ${(event.sim_time_ns / 1e9).toFixed(3)} s`}>{(event.sim_time_ns / 1e9).toFixed(3)} s</button> :
            <time title={`${event.sim_time_ns} ns`}>{(event.sim_time_ns / 1e9).toFixed(3)} s</time>}
          <div><strong>{event.title}</strong><span>{event.details}</span></div>
        </li>)}</ol>}
    </section>
  </section>
}
