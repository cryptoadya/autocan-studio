import { useEffect, useState } from 'react'
import type { SavedRun, Snapshot } from '../types'

interface Props {
  runs: SavedRun[]
  view: Snapshot | null
  busy: boolean
  onLoad: (id: string, time?: number) => void
  onRerun: (id: string) => void
  onLive: () => void
}

export function Recordings({runs, view, busy, onLoad, onRerun, onLive}: Props) {
  const [selected, setSelected] = useState('')
  const [time, setTime] = useState(0)
  useEffect(() => {
    if (view?.mode === 'playback') {
      setTime(view.sim_time_ns)
      setSelected(view.id)
    }
  }, [view])
  // A direct checkpoint can load before the background recording catalog arrives.
  const choices = view?.mode === 'playback' && !runs.some(run => run.id === view.id)
    ? [...runs, {id: view.id, duration_ns: view.duration_ns ?? view.sim_time_ns}] : runs
  return <section className="recordings">
    <div><h2>Recordings</h2><p>Verified playback reads saved events. Rerun computes them again.</p></div>
    <div className="recording-actions">
      <select aria-label="Saved recording" value={selected} onChange={e => setSelected(e.target.value)}>
        <option value="">{choices.length ? 'Select a saved run' : 'Stop & save to create a recording'}</option>
        {choices.map(run => <option key={run.id} value={run.id}>{run.id.slice(0, 8)} · {(run.duration_ns / 1e9).toFixed(2)} s</option>)}
      </select>
      <button className="secondary" disabled={!selected || busy} onClick={() => onLoad(selected)}>Load playback</button>
      <button className="secondary" disabled={!selected || busy} onClick={() => onRerun(selected)}>Deterministic rerun</button>
      {view?.mode === 'playback' && <button className="quiet" onClick={onLive}>Return to live</button>}
    </div>
    {view?.mode === 'playback' && <div className="playback-seek">
      <label htmlFor="playback-time">Playback position <span>{(time / 1e9).toFixed(2)} / {((view.duration_ns ?? 0) / 1e9).toFixed(2)} s</span></label>
      <input id="playback-time" type="range" min="0" max={view.duration_ns ?? 0} step="10000000" value={time}
        disabled={busy} onChange={e => setTime(Number(e.target.value))}
        onPointerUp={() => onLoad(view.id, time)} onKeyUp={() => onLoad(view.id, time)} />
    </div>}
  </section>
}
