import { useState } from 'react'
import useStore from '../store/useStore'
import { useWebSocket } from '../hooks/useWebSocket'

export default function RefreshStatusBadge() {
  const { status, refreshMode, refreshedAt, cacheHit, executionTime, rowCount, sessionId, refreshReport, isLoading } =
    useStore()
  const { connect, disconnect, connected } = useWebSocket()
  const [streaming, setStreaming] = useState(false)

  if (status !== 'success') return null

  const toggleStream = () => {
    if (streaming) {
      disconnect()
      setStreaming(false)
    } else {
      connect()
      setStreaming(true)
    }
  }

  const fmtTime = (ts) => {
    if (!ts) return ''
    try {
      return new Date(ts).toLocaleTimeString()
    } catch {
      return ts
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 mb-3">
      {/* Row count */}
      <span className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full bg-gray-100 text-gray-600">
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M3 10h18M3 14h18M3 6h18M3 18h18" />
        </svg>
        {rowCount.toLocaleString()} rows
      </span>

      {/* Execution time */}
      {executionTime > 0 && (
        <span className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full bg-gray-100 text-gray-600">
          ⚡ {executionTime.toFixed(2)}s
        </span>
      )}

      {/* Cache hit */}
      {cacheHit && (
        <span className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full bg-green-100 text-green-700">
          ✓ Cached
        </span>
      )}

      {/* Refresh mode + timestamp */}
      {refreshMode && refreshedAt && (
        <span className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full bg-blue-100 text-blue-700">
          <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
          Refreshed {fmtTime(refreshedAt)}
        </span>
      )}

      {/* Live stream indicator */}
      {connected && (
        <span className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full bg-emerald-100 text-emerald-700">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
          Live
        </span>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* Manual refresh */}
      {sessionId && (
        <button
          onClick={refreshReport}
          disabled={isLoading}
          className="inline-flex items-center gap-1 text-xs px-2.5 py-1.5 rounded border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition-colors"
        >
          <svg
            className={`w-3 h-3 ${isLoading ? 'animate-spin' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
            />
          </svg>
          Refresh
        </button>
      )}

      {/* Live stream toggle */}
      {sessionId && (
        <button
          onClick={toggleStream}
          className={`inline-flex items-center gap-1 text-xs px-2.5 py-1.5 rounded border transition-colors ${
            streaming
              ? 'border-emerald-400 text-emerald-700 bg-emerald-50 hover:bg-emerald-100'
              : 'border-gray-200 text-gray-600 hover:bg-gray-50'
          }`}
        >
          <span className={`w-1.5 h-1.5 rounded-full ${streaming ? 'bg-emerald-500 animate-pulse' : 'bg-gray-400'}`} />
          {streaming ? 'Stop Live' : 'Go Live'}
        </button>
      )}
    </div>
  )
}
