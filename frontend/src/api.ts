import { useEffect } from 'react'
import { useStudio } from './store'
import type { Snapshot, WireMessage } from './types'

export async function request<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method, headers: body === undefined ? {} : {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body), signal,
  })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`)
  return data as T
}

export function useLiveSocket(id: string | undefined) {
  useEffect(() => {
    if (!id) return
    let disposed = false
    let socket: WebSocket | undefined
    let timer: ReturnType<typeof setTimeout>
    let attempt = 0
    const connect = () => {
      socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/simulations/${id}`)
      socket.onmessage = event => {
        if (disposed) return
        try {
          const message = JSON.parse(event.data) as WireMessage
          if (message.id !== id || !useStudio.getState().ingest(message)) socket?.close()
          else attempt = 0
        } catch { socket?.close() }
      }
      socket.onclose = async () => {
        if (disposed) return
        useStudio.getState().setConnected(false)
        try {
          const current = await request<Snapshot | null>('/api/simulations/current')
          if (disposed) return
          if (current?.id !== id) {
            useStudio.getState().adopt(current)
            return
          }
        } catch { /* Backend may still be restarting; retry with bounded backoff. */ }
        if (!disposed) timer = setTimeout(connect, Math.min(500 * 2 ** attempt++, 5000))
      }
      socket.onerror = () => socket?.close()
    }
    connect()
    return () => {
      disposed = true
      clearTimeout(timer)
      socket?.close()
      useStudio.getState().setConnected(false)
    }
  }, [id])
}
