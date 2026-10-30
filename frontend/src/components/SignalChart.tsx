import { useLayoutEffect, useRef, useState } from 'react'
import type { Signal } from '../types'

export function SignalChart({signals, now}: {signals: Signal[]; now: number}) {
  const container = useRef<HTMLElement>(null)
  const [width, setWidth] = useState(1120)
  useLayoutEffect(() => {
    const observer = new ResizeObserver(entries => setWidth(Math.max(280, entries[0].contentRect.width - 30)))
    if (container.current) observer.observe(container.current)
    return () => observer.disconnect()
  }, [])
  const start = Math.max(0, now - 20e9)
  const end = Math.max(20e9, now)
  const x = (time: number) => 56 + (time - start) / (end - start) * (width - 80)
  const y = (rpm: number) => 196 - rpm / 8000 * 160
  const visible = signals.filter(s => s.sim_time_ns >= start)
  const segments: {status: string; points: string}[] = []
  for (let i = 1; i < visible.length; i++) {
    const a = visible[i - 1], b = visible[i]
    if (a.rpm === null || b.rpm === null) continue
    const last = segments[segments.length - 1]
    const point = `${x(b.sim_time_ns)},${y(b.rpm)}`
    if (last?.status === b.status) last.points += ` ${point}`
    else segments.push({status: b.status, points: `${x(a.sim_time_ns)},${y(a.rpm)} ${point}`})
  }
  return <section ref={container} className="chart-panel" aria-label="Received RPM chart">
    <div className="section-heading"><h2>Engine speed</h2><div className="legend"><i /> KOMBI measurement <span>20 s window · rpm</span></div></div>
    <svg viewBox={`0 0 ${width} 235`} role="img" aria-label="Received RPM against simulation time; dashed amber or red means stale data">
      {[0, 2000, 4000, 6000, 8000].map(rpm => <g key={rpm}>
        <line x1="56" x2={width - 24} y1={y(rpm)} y2={y(rpm)} className="grid-line" />
        <text x="42" y={y(rpm) + 4} textAnchor="end">{rpm.toLocaleString('en-US')}</text>
      </g>)}
      {[0, 5, 10, 15, 20].map(seconds => <text key={seconds} x={56 + seconds / 20 * (width - 80)} y="225" textAnchor="middle">
        {(start / 1e9 + seconds).toFixed(1)} s</text>)}
      {segments.map((segment, index) => <polyline key={index} points={segment.points}
        className={`signal-line ${segment.status.toLowerCase()}`} />)}
      {!segments.length && <text x={width / 2} y="120" textAnchor="middle">Run the demo to receive the first signal.</text>}
    </svg>
    <div className="chart-caption">Dashed segments retain the last measurement during STALE / TIMEOUT. Time is virtual.</div>
  </section>
}
