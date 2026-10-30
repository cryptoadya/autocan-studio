import { useEffect, useState } from 'react'
import { request, useLiveSocket } from './api'
import { useStudio } from './store'
import { Dashboard } from './components/Dashboard'
import { SignalChart } from './components/SignalChart'
import { CanMonitor } from './components/CanMonitor'
import { Recordings } from './components/Recordings'
import type { SavedRun, Snapshot } from './types'

export function App() {
  const live = useStudio(s => s.live)
  const playback = useStudio(s => s.playback)
  const connected = useStudio(s => s.connected)
  const skipped = useStudio(s => s.skipped)
  const [runs, setRuns] = useState<SavedRun[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(true)
  const view = playback ?? live
  const isPlayback = playback !== null
  useLiveSocket(live?.id)
  const refreshRuns = async () => setRuns(await request<SavedRun[]>('/api/runs'))
  useEffect(() => {
    let active = true
    Promise.all([request<Snapshot | null>('/api/simulations/current'), request<SavedRun[]>('/api/runs')])
      .then(([current, saved]) => {if (active) {if (current) useStudio.getState().adopt(current); setRuns(saved)}})
      .catch(e => {if (active) setError(String(e.message))})
      .finally(() => {if (active) setLoading(false)})
    return () => {active = false}
  }, [])
  useEffect(() => {
    if (live?.stopped) refreshRuns().catch(e => setError(e.message))
  }, [live?.stopped, live?.id])
  const perform = async (task: () => Promise<void>) => {
    setBusy(true); setError(''); setNotice('')
    try {await task()} catch (e) {setError(e instanceof Error ? e.message : String(e))}
    finally {setBusy(false)}
  }
  const control = (action: string) => perform(async () => {
    let session = live
    if (!session || (session.stopped && action === 'start')) {
      session = await request<Snapshot>('/api/simulations', 'POST', {seed: 42})
      useStudio.getState().adopt(session)
    }
    const next = await request<Snapshot>(`/api/simulations/${session.id}/${action}`, 'POST')
    // The ordered WS stream updates existing runs; a reset starts a new connection.
    if (next.id !== live?.id) useStudio.getState().adopt(next)
    if (action === 'stop' || action === 'reset') await refreshRuns()
  })
  const command = (path: string, method: string, body: unknown) => perform(async () => {
    if (!live) return
    await request(`/api/simulations/${live.id}/${path}`, method, body)
  })
  const load = (id: string, time?: number) => perform(async () => {
    if (live && !live.stopped && !live.state.paused) await request(`/api/simulations/${live.id}/pause`, 'POST')
    const saved = await request<Snapshot>(`/api/runs/${id}/playback${time === undefined ? '' : `?until_ns=${time}`}`)
    useStudio.getState().setPlayback(saved)
  })
  const disabled = busy || loading || isPlayback || !live || live.stopped || !connected
  return <>
    <header><div className="brand"><span className="brand-mark" aria-hidden="true">A<span>/</span>C</span>
      <span>AutoCAN <b>Studio</b></span></div><span className="header-note">Synthetic CAN laboratory <span>Local / DemoCar</span></span></header>
    <main>
      <div className="workspace-heading"><div><h1>Vehicle lab</h1><p>Engine ECU → CAN → KOMBI. One signal, the complete chain.</p></div>
        <div className="connection"><span className={`indicator ${!connected ? 'off' : ''}`} />{loading ? 'Connecting…' : connected ? 'Connected locally' : live ? 'Reconnecting…' : 'Ready locally'}</div></div>
      <div className="run-toolbar">
        <div className="run-identity"><strong className={isPlayback ? 'mode playback' : 'mode'}>{isPlayback ? 'PLAYBACK' : 'LIVE'}</strong>
          <span className="run-id">{view ? `Run ${view.id.slice(0, 8)}` : 'DemoCar · seed 42'}</span>
          <span className="sim-time">{((view?.sim_time_ns ?? 0) / 1e9).toFixed(2)} <small>s virtual</small></span></div>
        <div className="run-actions">
          <button className="primary" disabled={busy || loading || isPlayback || (!!live && !connected)}
            onClick={() => control(live && !live.stopped && !live.state.paused ? 'pause' : 'start')}>
            {!live || live.stopped ? 'Run demo' : live.state.paused ? 'Resume' : 'Pause'}</button>
          <button className="secondary" disabled={disabled} onClick={() => control('stop')}>Stop & save</button>
          <button className="quiet" disabled={busy || loading || isPlayback || !live || !connected} onClick={() => control('reset')}>Reset · new recording</button>
        </div>
      </div>
      {(error || view?.error) && <div role="alert" className="message error">{error || view?.error}</div>}
      {notice && <div role="status" className="message success">{notice}</div>}
      {isPlayback && <div className="playback-banner">Viewing saved events. Live controls are disabled; the simulation is paused.</div>}
      <Dashboard key={view?.id} state={view?.state} disabled={disabled}
        onThrottle={value => command('inputs', 'PATCH', {throttle: value})}
        onOffline={offline => command('faults', 'POST', {offline})} />
      <SignalChart signals={view?.signals ?? []} now={view?.sim_time_ns ?? 0} />
      <CanMonitor key={`${view?.id}-${view?.mode}`} frames={view?.frames ?? []} skipped={skipped} />
      <Recordings runs={runs} view={view} busy={busy} onLoad={load}
        onLive={() => useStudio.getState().setPlayback(null)}
        onRerun={id => perform(async () => {
          const result = await request<{matches: boolean; event_count: number}>(`/api/runs/${id}/rerun`, 'POST')
          if (!result.matches) throw new Error('Rerun did not match the recording')
          setNotice(`Deterministic rerun matched · ${result.event_count.toLocaleString()} canonical events`)
        })} />
      <footer><span>{view ? `${view.recording === 'recording' ? '● Recording' : view.recording === 'saved' ? 'Saved' : 'Recording failed'} · ${view.id.slice(0, 8)}` : 'New runs are recorded from time zero.'}</span>
        <span>120 s / run · 256 commands · JSONL · Synthetic profile, no vehicle hardware</span></footer>
    </main>
  </>
}
