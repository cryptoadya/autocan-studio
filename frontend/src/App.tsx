import { useCallback, useEffect, useRef, useState } from 'react'
import { request, useLiveSocket } from './api'
import { useStudio } from './store'
import { Dashboard } from './components/Dashboard'
import { AnomalyChart } from './components/AnomalyChart'
import { MotionControls } from './components/MotionControls'
import { NetworkPanel } from './components/NetworkPanel'
import { SignalChart } from './components/SignalChart'
import { CanMonitor } from './components/CanMonitor'
import { Recordings } from './components/Recordings'
import { FaultLab } from './components/FaultLab'
import { ScenarioControls } from './components/ScenarioControls'
import type { DemoScenario, FaultCatalog, SavedRun, Snapshot } from './types'

export function App() {
  const live = useStudio(s => s.live)
  const playback = useStudio(s => s.playback)
  const connected = useStudio(s => s.connected)
  const skipped = useStudio(s => s.skipped)
  const [runs, setRuns] = useState<SavedRun[]>([])
  const [scenarios, setScenarios] = useState<DemoScenario[]>([])
  const [faultCatalog, setFaultCatalog] = useState<FaultCatalog | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(true)
  const playbackRequest = useRef<AbortController | null>(null)
  const runsRequest = useRef<AbortController | null>(null)
  useEffect(() => () => {
    playbackRequest.current?.abort()
    playbackRequest.current = null
  }, [])
  const view = playback ?? live
  const isPlayback = playback !== null
  useLiveSocket(live?.id)
  const refreshRuns = useCallback(() => {
    runsRequest.current?.abort()
    const controller = new AbortController()
    runsRequest.current = controller
    request<SavedRun[]>('/api/runs', 'GET', undefined, controller.signal)
      .then(saved => {if (!controller.signal.aborted) setRuns(saved)})
      .catch(e => {if (!controller.signal.aborted) setError(e.message)})
  }, [])
  useEffect(() => {
    refreshRuns()
    return () => runsRequest.current?.abort()
  }, [refreshRuns])
  useEffect(() => {
    let active = true
    Promise.all([request<Snapshot | null>('/api/simulations/current'),
      request<DemoScenario[]>('/api/scenarios'), request<FaultCatalog>('/api/faults')])
      .then(([current, demos, faults]) => {if (active) {if (current) useStudio.getState().adopt(current); setScenarios(demos); setFaultCatalog(faults)}})
      .catch(e => {if (active) setError(String(e.message))})
      .finally(() => {if (active) setLoading(false)})
    return () => {active = false}
  }, [])
  useEffect(() => {
    if (live?.stopped) refreshRuns()
  }, [live?.stopped, live?.id, refreshRuns])
  const perform = async (task: () => Promise<void>) => {
    setBusy(true); setError(''); setNotice('')
    try {await task()} catch (e) {setError(e instanceof Error ? e.message : String(e))}
    finally {setBusy(false)}
  }
  const control = (action: string) => perform(async () => {
    let session = live
    if (!session || (session.stopped && action === 'start')) {
      session = await request<Snapshot>('/api/simulations', 'POST', {seed: 42, profile_id: 'democar-network'})
      useStudio.getState().adopt(session)
    }
    const next = await request<Snapshot>(`/api/simulations/${session.id}/${action}`, 'POST')
    // The ordered WS stream updates existing runs; a reset starts a new connection.
    if (next.id !== live?.id) useStudio.getState().adopt(next)
    if (action === 'reset') refreshRuns()
  })
  const command = (path: string, method: string, body: unknown) => perform(async () => {
    if (!live) return
    await request(`/api/simulations/${live.id}/${path}`, method, body)
  })
  const load = async (id: string, time?: number) => {
    playbackRequest.current?.abort()
    const controller = new AbortController()
    playbackRequest.current = controller
    setBusy(true); setError(''); setNotice('')
    try {
      if (live && !live.stopped && !live.state.paused) {
        await request(`/api/simulations/${live.id}/pause`, 'POST', undefined, controller.signal)
      }
      if (controller.signal.aborted) return
      const saved = await request<Snapshot>(
        `/api/runs/${id}/playback${time === undefined ? '' : `?until_ns=${time}`}`,
        'GET', undefined, controller.signal,
      )
      if (!controller.signal.aborted) useStudio.getState().setPlayback(saved)
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof Error ? e.message : String(e))
    } finally {
      // A cancelled load must not clear the busy state of a newer operation.
      if (playbackRequest.current === controller) {
        playbackRequest.current = null
        setBusy(false)
      }
    }
  }
  const returnToLive = () => {
    if (playbackRequest.current) {
      playbackRequest.current.abort()
      playbackRequest.current = null
      setBusy(false)
    }
    useStudio.getState().setPlayback(null)
  }
  const disabled = busy || loading || isPlayback || !live || live.stopped || !connected
  return <>
    <header><div className="brand"><span className="brand-mark" aria-hidden="true">A<span>/</span>C</span>
      <span>AutoCAN <b>Studio</b></span></div><span className="header-note">Synthetic CAN laboratory <span>Local / DemoCar</span></span></header>
    <main>
      <div className="workspace-heading"><div><h1>Vehicle lab</h1><p>{view && view.state.profile_id !== 'democar-network' ? 'Engine ECU → CAN → KOMBI. One signal, the complete chain.' : 'Engine · ABS/DSC · Transmission · Body · KOMBI. Connected through CAN.'}</p></div>
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
      <ScenarioControls scenarios={scenarios} view={view} disabled={busy || loading || isPlayback || !!(live && !live.stopped)}
        onRun={(scenario_id, seed) => perform(async () => {
          const session = await request<Snapshot>('/api/simulations', 'POST', {scenario_id, seed})
          useStudio.getState().adopt(session)
          await request(`/api/simulations/${session.id}/start`, 'POST')
        })} />
      <Dashboard key={view?.id} state={view?.state} disabled={disabled}
        onThrottle={value => command('inputs', 'PATCH', {throttle: value})}
        onIgnition={ignition => command('inputs', 'PATCH', {ignition})}
        onEngineRunning={engine_running => command('inputs', 'PATCH', {engine_running})}
        onOffline={offline => command('faults', 'POST', {offline})} />
      <MotionControls key={`motion-${view?.id}`} state={view?.state} disabled={disabled}
        onBrake={brake => command('inputs', 'PATCH', {brake})}
        onGear={gear => command('inputs', 'PATCH', {gear})} />
      {view?.state.profile_id === 'democar-network' && <NetworkPanel state={view.state} disabled={disabled}
        onOffline={(ecu_id, offline) => command('faults', 'POST', {ecu_id, offline})} />}
      {view?.state.profile_id === 'democar-network' && <AnomalyChart view={view} />}
      <FaultLab key={`faults-${view?.id}-${view?.mode}`} catalog={faultCatalog} view={view}
        disabled={disabled || view?.state.profile_id !== 'democar-network'}
        onInject={fault => command('faults', 'POST', {fault})}
        onClear={id => command(`faults/${encodeURIComponent(id)}/clear`, 'POST', undefined)}
        onSeek={time => {if (view) void load(view.id, time)}} />
      <SignalChart signals={view?.signals ?? []} now={view?.sim_time_ns ?? 0} />
      <CanMonitor key={`${view?.id}-${view?.mode}`} frames={view?.frames ?? []} skipped={skipped} network={view?.state.profile_id === 'democar-network'} />
      <Recordings runs={runs} view={view} busy={busy} onLoad={load}
        onLive={returnToLive}
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
