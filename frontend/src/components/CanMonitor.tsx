import { useState } from 'react'
import type { Frame } from '../types'

export function CanMonitor({frames, skipped, network = false}: {frames: Frame[]; skipped: number; network?: boolean}) {
  const [frozen, setFrozen] = useState<Frame[] | null>(null)
  const [filter, setFilter] = useState('')
  const shown = (frozen ?? frames).filter(f => `0x${f.can_id.toString(16)}`.includes(filter.toLowerCase())).slice(-50).reverse()
  return <section className="monitor-panel">
    <div className="section-heading"><h2>CAN Monitor <span>{network ? 'Delivered to CAN observer' : 'Delivered to KOMBI'}</span></h2>
      <div className="monitor-tools"><input aria-label="Filter CAN ID" placeholder="Filter ID, e.g. 0x200"
        value={filter} onChange={e => setFilter(e.target.value)} />
        <button className="quiet" onClick={() => setFrozen(frozen ? null : [...frames])}>{frozen ? 'Unfreeze' : 'Freeze'}</button></div>
    </div>
    <div className="table-scroll"><table><thead><tr><th>SIM TIME / s</th><th>CAN ID</th><th>DLC</th><th>PAYLOAD / hex</th><th>{network ? 'DECODED SIGNALS' : 'DECODED RPM'}</th><th>FRAME</th></tr></thead>
      <tbody>{shown.map(frame => <tr key={frame.frame_id}>
        <td>{(frame.sim_time_ns / 1e9).toFixed(3)}</td><td className="can-id">0x{frame.can_id.toString(16).toUpperCase()}</td>
        <td>{frame.dlc}</td><td>{frame.payload_hex.match(/../g)?.join(' ').toUpperCase()}</td>
        <td className="decoded-signals">{frame.decoded ? <><b>{frame.message_name}</b> {frame.decoded}</> : frame.rpm?.toFixed(1) ?? '—'}</td><td className="muted">#{frame.frame_id}</td>
      </tr>)}</tbody></table>
      {!shown.length && <p className="table-empty">{filter ? 'No matching frames in the current buffer.' : 'Waiting for CAN frames. Start the demo above.'}</p>}
    </div>
    <div className="monitor-footer"><span>{frozen ? 'Display frozen · simulation continues' : 'Latest 50 deliveries · 200-frame rolling buffer'}</span>
      <span>{skipped > 0 ? `${skipped} live display frames skipped; recording is complete` : 'Canonical recording keeps every event'}</span></div>
  </section>
}
