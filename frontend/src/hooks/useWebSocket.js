import { useRef, useCallback, useState } from 'react'
import { createWebSocket } from '../api/client'
import useStore from '../store/useStore'

export function useWebSocket() {
  const wsRef = useRef(null)
  const [connected, setConnected] = useState(false)
  const applyStreamData = useStore((s) => s.applyStreamData)
  const _handleError = useStore((s) => s._handleError)
  const sessionId = useStore((s) => s.sessionId)
  const userId = useStore((s) => s.userId)
  const userRole = useStore((s) => s.userRole)

  const connect = useCallback(() => {
    if (!sessionId || wsRef.current) return

    const ws = createWebSocket(sessionId, userId, userRole)
    wsRef.current = ws

    ws.onopen = () => setConnected(true)

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        applyStreamData(data)
      } catch (e) {
        console.error('WebSocket parse error', e)
      }
    }

    ws.onerror = () => {
      setConnected(false)
      wsRef.current = null
    }

    ws.onclose = (event) => {
      setConnected(false)
      wsRef.current = null
      if (event.code === 1008) {
        _handleError({ message: event.reason || 'Live stream closed — session error.' })
      }
    }
  }, [sessionId, userId, userRole, applyStreamData, _handleError])

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
      setConnected(false)
    }
  }, [])

  return { connect, disconnect, connected }
}
