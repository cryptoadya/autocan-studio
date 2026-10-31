import { useState } from 'react'
import type { DemoScenario, Snapshot } from '../types'

export function ScenarioControls({scenarios, view, disabled, onRun}: {
  scenarios: DemoScenario[]; view: Snapshot | null; disabled: boolean
  onRun: (id: string, seed: number) => void
}) {
  const [selected, setSelected] = useState('rpm_spike')
  const [seed, setSeed] = useState('42')
  const scenario = scenarios.find(s => s.id === selected) ?? scenarios[0]
  return <section className="scenario-controls" aria-label="Demo scenarios">
    <div><h2>Fault scenarios</h2><p>{view?.scenario ? `${view.scenario.name} · ${(view.scenario.duration_ns / 1e9).toFixed(2)} s · ${view.stopped ? 'Saved' : 'Ready / running'}` : 'Three original, reproducible demonstrations.'}</p></div>
    <form onSubmit={event => {event.preventDefault(); if (scenario && !disabled) onRun(scenario.id, Number(seed))}}>
      <label>Scenario<select value={scenario?.id ?? ''} disabled={disabled} onChange={e => setSelected(e.target.value)}>
        {scenarios.map(s => <option key={s.id} value={s.id}>{s.name} · {(s.duration_ns / 1e9).toFixed(2)} s</option>)}
      </select></label>
      <label>Scenario seed<input type="number" value={seed} min="0" max="4294967295" step="1" required disabled={disabled} onChange={e => setSeed(e.target.value)} /></label>
      <button className="secondary" disabled={disabled || !scenario}>Run scenario</button>
    </form>
    <p className="scenario-description">{scenario?.description} {view && !view.stopped && view.mode === 'live' ? 'Stop & save the current run to choose another scenario.' : 'Runs save automatically at their exact duration. Use playback to inspect each interval.'}</p>
  </section>
}
