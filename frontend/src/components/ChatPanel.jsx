import { useState, useRef, useEffect, useCallback } from 'react'
import useStore from '../store/useStore'

// ─── Constants ────────────────────────────────────────────────────────────────

const DEFAULT_WIDTH = 340
const MIN_WIDTH     = 260
const MAX_WIDTH     = 600

// ─── Inline chat message types ───────────────────────────────────────────────

const STATUS_META = {
  greeting:        { icon: '👋', label: 'Hello!',      bg: 'bg-blue-50 border-blue-200',  text: 'text-blue-800' },
  off_topic:       { icon: '🤔', label: 'Off Topic',   bg: 'bg-gray-50 border-gray-200',  text: 'text-gray-700' },
  filter_redirect: { icon: '🔍', label: 'Use Filters', bg: 'bg-blue-50 border-blue-200',  text: 'text-blue-800' },
}

const ERROR_META = {
  SESSION_EXPIRED: { icon: '⏰', label: 'Session Expired', color: 'bg-amber-50 border-amber-300 text-amber-800'  },
  ACCESS_DENIED:   { icon: '🔒', label: 'Access Denied',   color: 'bg-red-50 border-red-300 text-red-800'        },
  SCHEMA_CHANGED:  { icon: '🔄', label: 'Schema Changed',  color: 'bg-orange-50 border-orange-300 text-orange-800' },
}

const CHIP_QUERIES = [
  'Show all active KRAs',
  'Employee productivity',
  'Top 10 by completion rate',
  'KRAs pending approval',
  'List KRAs by manager',
]

// ─── Sub-components ───────────────────────────────────────────────────────────

function BotMessageBubble({ meta, message, suggestions, onSuggestion, onDismiss }) {
  return (
    <div className={`mx-3 mb-2 rounded-xl border p-3 ${meta.bg}`}>
      <div className="flex items-start gap-2">
        <span className="text-lg leading-none flex-shrink-0 mt-0.5">{meta.icon}</span>
        <div className="flex-1 min-w-0">
          <p className={`text-xs font-semibold mb-0.5 ${meta.text}`}>{meta.label}</p>
          {message && <p className={`text-xs ${meta.text} opacity-90`}>{message}</p>}
          {suggestions?.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {suggestions.map((s, i) => (
                <button
                  key={i}
                  onClick={() => onSuggestion(s)}
                  className="text-xs px-2 py-1 bg-white border border-blue-300 text-blue-700 rounded-full hover:bg-blue-50 transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          )}
        </div>
        <button onClick={onDismiss} className="text-gray-400 hover:text-gray-600 text-base leading-none flex-shrink-0">×</button>
      </div>
    </div>
  )
}

function ErrorBubble({ error, errorCode, onDismiss }) {
  const meta = ERROR_META[errorCode] || { icon: '⚠️', label: 'Error', color: 'bg-red-50 border-red-300 text-red-800' }
  return (
    <div className={`mx-3 mb-2 flex items-start gap-2 px-3 py-2.5 rounded-xl border ${meta.color}`}>
      <span className="text-sm leading-none flex-shrink-0 mt-0.5">{meta.icon}</span>
      <div className="flex-1 min-w-0">
        <p className="text-xs font-semibold">{meta.label}</p>
        <p className="text-xs mt-0.5 opacity-90">{error}</p>
      </div>
      <button onClick={onDismiss} className="opacity-60 hover:opacity-100 text-base leading-none flex-shrink-0">×</button>
    </div>
  )
}

function ClarificationCard({ clarification, clarifyReport, isLoading }) {
  const [customAnswer, setCustomAnswer] = useState('')
  const { follow_up_question, follow_up_options, clarification_round } = clarification

  const handleOption = (opt) => { setCustomAnswer(''); clarifyReport(opt) }

  const handleCustom = (e) => {
    e.preventDefault()
    const trimmed = customAnswer.trim()
    if (!trimmed || isLoading) return
    setCustomAnswer('')
    clarifyReport(trimmed)
  }

  return (
    <div className="mx-3 mb-2 rounded-xl border border-blue-200 bg-blue-50 p-3.5">
      <div className="flex items-center gap-2 mb-2.5">
        <div className="w-6 h-6 rounded-full bg-blue-600 flex items-center justify-center flex-shrink-0">
          <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M13 10V3L4 14h7v7l9-11h-7z" />
          </svg>
        </div>
        <div>
          <p className="text-xs font-semibold text-blue-700 leading-tight">Clarification needed</p>
          {clarification_round > 0 && (
            <p className="text-xs text-blue-400 leading-tight">Round {clarification_round + 1} of 2</p>
          )}
        </div>
      </div>

      <p className="text-sm font-medium text-blue-900 mb-3 leading-snug">{follow_up_question}</p>

      {follow_up_options?.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {follow_up_options.map((opt, i) => (
            <button
              key={i}
              type="button"
              onClick={() => handleOption(opt)}
              disabled={isLoading}
              className="px-2.5 py-1 text-xs bg-white border border-blue-300 text-blue-700 rounded-lg hover:bg-blue-100 hover:border-blue-400 disabled:opacity-50 transition-colors"
            >
              {opt}
            </button>
          ))}
        </div>
      )}

      <form onSubmit={handleCustom} className="flex gap-1.5">
        <input
          type="text"
          value={customAnswer}
          onChange={(e) => setCustomAnswer(e.target.value)}
          disabled={isLoading}
          placeholder="Or type your own answer…"
          className="flex-1 text-xs border border-blue-200 rounded-lg px-2.5 py-1.5 bg-white focus:outline-none focus:border-blue-500 placeholder-blue-300 text-blue-800"
        />
        <button
          type="submit"
          disabled={!customAnswer.trim() || isLoading}
          className="px-3 py-1.5 text-xs bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors font-medium"
        >
          Send
        </button>
      </form>
    </div>
  )
}

function LoadingBubble() {
  return (
    <div className="mx-3 mb-2 flex items-center gap-2">
      <div className="w-6 h-6 rounded-full bg-blue-600 flex items-center justify-center flex-shrink-0">
        <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin block" />
      </div>
      <div className="bg-blue-50 border border-blue-100 rounded-xl px-3 py-2">
        <p className="text-xs text-blue-700">Generating your report…</p>
        <p className="text-xs text-blue-400 mt-0.5">Running SQL against the database</p>
      </div>
    </div>
  )
}

function EmptyChatState() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-4 pb-4">
      <div className="w-10 h-10 rounded-xl bg-blue-50 flex items-center justify-center mb-2.5">
        <svg className="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
        </svg>
      </div>
      <p className="text-xs font-medium text-gray-500 mb-1">Start a conversation</p>
      <p className="text-xs text-gray-400 leading-relaxed">
        Type a question or tap a suggestion below to generate a KRA report.
      </p>
    </div>
  )
}

// ─── Main ChatPanel ───────────────────────────────────────────────────────────

export default function ChatPanel() {
  const [minimized, setMinimized]   = useState(false)
  const [query, setQuery]           = useState('')
  const [panelWidth, setPanelWidth] = useState(DEFAULT_WIDTH)

  const {
    status, isLoading, hasData,
    clarification, message, suggestions,
    error, errorCode,
    clearError, clearMessage, clearFilters,
    generateReport, clarifyReport, reset,
  } = useStore()

  const textareaRef   = useRef(null)
  const chatEndRef    = useRef(null)
  const isDragging    = useRef(false)
  const dragStartX    = useRef(0)
  const dragStartW    = useRef(0)

  const showMessage = ['greeting', 'off_topic', 'filter_redirect'].includes(status)

  // Auto-scroll chat area when new content appears
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [clarification, error, status, isLoading])

  // ── Drag-to-resize ─────────────────────────────────────────────────────────
  const onDragStart = useCallback((e) => {
    isDragging.current  = true
    dragStartX.current  = e.clientX
    dragStartW.current  = panelWidth
    document.body.style.cursor     = 'col-resize'
    document.body.style.userSelect = 'none'
    e.preventDefault()
  }, [panelWidth])

  useEffect(() => {
    const onMouseMove = (e) => {
      if (!isDragging.current) return
      // Panel is on the right; dragging the left edge leftward widens it
      const delta    = dragStartX.current - e.clientX
      const newWidth = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, dragStartW.current + delta))
      setPanelWidth(newWidth)
    }

    const onMouseUp = () => {
      if (!isDragging.current) return
      isDragging.current          = false
      document.body.style.cursor     = ''
      document.body.style.userSelect = ''
    }

    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup',   onMouseUp)
    return () => {
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup',   onMouseUp)
    }
  }, [])

  // ── Form handlers ──────────────────────────────────────────────────────────
  const handleSubmit = async (e) => {
    e?.preventDefault()
    const trimmed = query.trim()
    if (!trimmed || isLoading) return
    clearFilters()
    setQuery('')
    await generateReport(trimmed)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSubmit(e) }
  }

  const handleChip = (q) => { setQuery(q); textareaRef.current?.focus() }

  const handleSuggestion = (q) => { clearMessage(); generateReport(q) }

  const handleClear = () => { setQuery(''); reset() }

  // ── Minimized strip ────────────────────────────────────────────────────────
  if (minimized) {
    return (
      <div className="flex flex-col items-center pt-3 w-10 border-l border-gray-200 bg-white flex-shrink-0">
        <button
          onClick={() => setMinimized(false)}
          title="Open chat"
          className="p-2 rounded-lg hover:bg-blue-50 text-gray-400 hover:text-blue-600 transition-colors"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
          </svg>
        </button>
      </div>
    )
  }

  // ── Full panel ─────────────────────────────────────────────────────────────
  return (
    <div
      className="relative flex flex-col flex-shrink-0 border-l border-gray-200 bg-white shadow-sm"
      style={{ width: panelWidth }}
    >
      {/* ── Drag handle (left edge) ─────────────────────────────────────── */}
      <div
        onMouseDown={onDragStart}
        title="Drag to resize"
        className="absolute left-0 top-0 bottom-0 w-1.5 cursor-col-resize z-10 group"
      >
        {/* Visible indicator on hover */}
        <div className="absolute inset-y-0 left-0 w-0.5 bg-transparent group-hover:bg-blue-400 transition-colors" />
      </div>

      {/* ── Panel header ───────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 flex-shrink-0">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-md bg-blue-600 flex items-center justify-center">
            <svg className="w-3.5 h-3.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          </div>
          <span className="text-sm font-semibold text-gray-800">Ask your question</span>
        </div>
        {/* Icon-only minimize button */}
        <button
          onClick={() => setMinimized(true)}
          title="Minimize"
          className="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-md transition-colors"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" />
          </svg>
        </button>
      </div>

      {/* ── Chat messages area (scrollable) ────────────────────────────── */}
      <div className="flex-1 overflow-y-auto scrollbar-thin py-3 flex flex-col">

        {!isLoading && !showMessage && !clarification && !error && <EmptyChatState />}

        {error && (
          <ErrorBubble error={error} errorCode={errorCode} onDismiss={clearError} />
        )}

        {showMessage && message && (
          <BotMessageBubble
            meta={STATUS_META[status]}
            message={message}
            suggestions={suggestions}
            onSuggestion={handleSuggestion}
            onDismiss={clearMessage}
          />
        )}

        {isLoading && <LoadingBubble />}

        {clarification && !isLoading && (
          <ClarificationCard
            clarification={clarification}
            clarifyReport={clarifyReport}
            isLoading={isLoading}
          />
        )}

        <div ref={chatEndRef} />
      </div>

      {/* ── Suggested query chips ──────────────────────────────────────── */}
      <div className="px-3 pt-2 pb-1.5 border-t border-gray-100 flex-shrink-0">
        <div className="flex flex-wrap gap-1.5">
          {CHIP_QUERIES.map((q) => (
            <button
              key={q}
              onClick={() => handleChip(q)}
              className="text-xs px-2.5 py-1 rounded-full border border-gray-200 text-gray-500 hover:border-blue-400 hover:text-blue-600 hover:bg-blue-50 transition-colors whitespace-nowrap"
            >
              {q}
            </button>
          ))}
        </div>
      </div>

      {/* ── Input bar (pinned at bottom) ───────────────────────────────── */}
      <div className="px-3 pb-3 pt-2 border-t border-gray-200 flex-shrink-0">
        <form onSubmit={handleSubmit} className="flex items-end gap-2">
          <textarea
            ref={textareaRef}
            rows={2}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isLoading}
            placeholder="Ask a question about your KRA data… (Shift+Enter for new line)"
            className="flex-1 resize-none rounded-lg border border-gray-300 px-3 py-2 text-xs text-gray-800 placeholder-gray-400 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 disabled:bg-gray-50 disabled:text-gray-400 scrollbar-thin"
          />

          {/* Run + Clear stacked on the right */}
          <div className="flex flex-col gap-1.5 flex-shrink-0">
            <button
              type="submit"
              disabled={!query.trim() || isLoading}
              className="flex items-center justify-center gap-1.5 px-3 py-2 bg-blue-600 text-white text-xs font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {isLoading ? (
                <span className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
              ) : (
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
              )}
              {isLoading ? 'Running…' : 'Run'}
            </button>

            {(hasData || query) && (
              <button
                type="button"
                onClick={handleClear}
                className="px-3 py-1.5 text-xs text-gray-500 border border-gray-200 rounded-lg hover:bg-gray-50 hover:text-gray-700 transition-colors"
              >
                Clear
              </button>
            )}
          </div>
        </form>

        <p className="mt-1.5 text-xs text-gray-400 text-right">
          KRA data · GPT-4o-mini · MySQL
        </p>
      </div>
    </div>
  )
}
