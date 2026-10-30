import { create } from 'zustand'
import type { Snapshot, WireMessage } from './types'

interface StudioState {
  live: Snapshot | null
  playback: Snapshot | null
  seq: number
  skipped: number
  connected: boolean
  adopt: (snapshot: Snapshot | null) => void
  ingest: (message: WireMessage) => boolean
  setPlayback: (snapshot: Snapshot | null) => void
  setConnected: (value: boolean) => void
  clear: () => void
}

export const useStudio = create<StudioState>((set, get) => ({
  live: null, playback: null, seq: 0, skipped: 0, connected: false,
  adopt: snapshot => set({live: snapshot, playback: null, seq: 0}),
  setPlayback: playback => set({playback}),
  setConnected: connected => set({connected}),
  clear: () => set({live: null, playback: null, seq: 0, skipped: 0, connected: false}),
  ingest: message => {
    const previous = get()
    if (message.schema_version !== 1 ||
      (message.type !== 'snapshot' && (!previous.live || previous.live.id !== message.id ||
        message.seq !== previous.seq + 1))) return false
    const snapshot = message.type === 'snapshot'
    set({
      live: {
        ...message,
        frames: (snapshot ? message.frames : [...previous.live!.frames, ...message.frames]).slice(-200),
        signals: (snapshot ? message.signals : [...previous.live!.signals, ...message.signals]).slice(-400),
      },
      seq: message.seq, connected: true,
      skipped: snapshot ? previous.skipped : previous.skipped + message.skipped_frames,
    })
    return true
  },
}))
