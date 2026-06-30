import { useState, useRef, useEffect, useCallback } from 'react'
import useStore from '../store/useStore'

// ─── Constants ────────────────────────────────────────────────────────────────

const DEFAULT_WIDTH = 400
const MIN_WIDTH     = 300
const MAX_WIDTH     = 720

const QUICK_PROMPTS = [
  'Show all active KRAs',
  'Top 10 by completion rate',
  'KRAs pending approval',
  'List KRAs by manager',
]

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmtTime(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

// ─── Bot avatar ───────────────────────────────────────────────────────────────

function BotAvatar() {
  return (
    <div className="w-7 h-7 rounded-full bg-blue-600 flex items-center justify-center flex-shrink-0 mt-0.5">
      <svg className="w-3.5 h-3.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M13 10V3L4 14h7v7l9-11h-7z" />
      </svg>
    </div>
  )
}

// ─── User bubble ──────────────────────────────────────────────────────────────

function UserBubble({ msg }) {
  return (
    <div className="flex flex-col items-end mb-4 px-3">
      <div className="max-w-[88%] bg-blue-600 text-white rounded-2xl rounded-tr-sm px-4 py-2.5 shadow-sm">
        <p className="text-sm leading-relaxed whitespace-pre-wrap break-words">{msg.content}</p>
      </div>
      <span className="text-[11px] text-gray-400 mt-1 mr-1">{fmtTime(msg.ts)}</span>
    </div>
  )
}

// ─── Thinking bubble ──────────────────────────────────────────────────────────

function ThinkingBubble() {
  return (
    <div className="flex items-start gap-2 mb-4 px-3">
      <BotAvatar />
      <div className="bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm flex items-center gap-2">
        <span className="text-sm text-gray-600 font-medium">Thinking</span>
        <span className="flex items-end gap-0.5 pb-px">
          <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-bounce [animation-delay:0ms]" />
          <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-bounce [animation-delay:150ms]" />
          <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-bounce [animation-delay:300ms]" />
        </span>
      </div>
    </div>
  )
}

// ─── Assistant: report bubble (text summary only — table renders in left panel) ─

function ReportBubble({ msg }) {
  const { snapshot, ts } = msg
  const { rowCount, totalRows } = snapshot || {}
  const count = totalRows ?? rowCount ?? 0

  return (
    <div className="flex items-start gap-2 mb-4 px-3">
      <BotAvatar />
      <div className="flex-1 min-w-0 bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm">

        {/* Status line */}
        <div className="flex items-center gap-1.5 mb-2">
          <svg className="w-3.5 h-3.5 text-green-500 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
          </svg>
          <span className="text-xs font-semibold text-green-700">Report generated successfully.</span>
        </div>

        {/* Row count + left-panel hint */}
        {count > 0 ? (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="inline-flex items-center gap-1 text-xs bg-blue-50 text-blue-700 border border-blue-200 rounded-full px-2.5 py-0.5 font-medium">
              <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                <path d="M5 3a2 2 0 00-2 2v2a2 2 0 002 2h2a2 2 0 002-2V5a2 2 0 00-2-2H5zM5 11a2 2 0 00-2 2v2a2 2 0 002 2h2a2 2 0 002-2v-2a2 2 0 00-2-2H5zM11 5a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V5zM14 11a1 1 0 011 1v1h1a1 1 0 110 2h-1v1a1 1 0 11-2 0v-1h-1a1 1 0 110-2h1v-1a1 1 0 011-1z" />
              </svg>
              {count.toLocaleString()} row{count !== 1 ? 's' : ''}
            </span>
            <span className="text-xs text-gray-400 flex items-center gap-1">
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
              </svg>
              See full report on the left
            </span>
          </div>
        ) : (
          <p className="text-sm text-gray-400 flex items-center gap-1.5">
            <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            No results found for this query.
          </p>
        )}

        <span className="text-[11px] text-gray-400 mt-2.5 block">{fmtTime(ts)}</span>
      </div>
    </div>
  )
}

// ─── Assistant: text bubble (greeting / off_topic / filter_redirect) ──────────

const STATUS_ICON = { greeting: '👋', off_topic: '🤔', filter_redirect: '🔍', restricted_operation: '🚫' }

function TextBubble({ msg, onSuggestion }) {
  const { content, suggestions, status, ts } = msg
  const icon = STATUS_ICON[status] || '💬'

  return (
    <div className="flex items-start gap-2 mb-4 px-3">
      <BotAvatar />
      <div className="flex-1 min-w-0 bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm">
        <div className="flex items-start gap-1.5">
          <span className="text-base leading-none flex-shrink-0">{icon}</span>
          <p className="text-sm text-gray-700 leading-relaxed">{content}</p>
        </div>

        {suggestions?.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-2.5">
            {suggestions.map((s, i) => (
              <button
                key={i}
                onClick={() => onSuggestion(s)}
                className="text-xs px-2.5 py-1 bg-blue-50 border border-blue-200 text-blue-700 rounded-full hover:bg-blue-100 transition-colors"
              >
                {s}
              </button>
            ))}
          </div>
        )}

        <span className="text-[11px] text-gray-400 mt-2 block">{fmtTime(ts)}</span>
      </div>
    </div>
  )
}

// ─── Assistant: clarification bubble ─────────────────────────────────────────

function ClarificationBubble({ msg, clarifyReport, isLoading }) {
  const { clarification, answered, selectedAnswer, ts } = msg
  const { follow_up_question, follow_up_options, clarification_round } = clarification || {}
  const [custom, setCustom] = useState('')

  const pick = (opt) => { if (answered || isLoading) return; clarifyReport(opt) }

  const submit = (e) => {
    e.preventDefault()
    const t = custom.trim()
    if (!t || answered || isLoading) return
    setCustom('')
    clarifyReport(t)
  }

  return (
    <div className="flex items-start gap-2 mb-4 px-3">
      <BotAvatar />
      <div className="flex-1 min-w-0 bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm">
        <div className="flex items-center gap-1.5 mb-1.5">
          <span className="text-xs font-semibold text-blue-600 uppercase tracking-wide">Clarification needed</span>
          {clarification_round > 0 && (
            <span className="text-xs text-gray-400">· Round {clarification_round + 1}</span>
          )}
        </div>

        <p className="text-sm font-medium text-gray-800 leading-snug mb-3">{follow_up_question}</p>

        {answered ? (
          <div className="flex items-center gap-1.5 text-xs text-gray-400 italic">
            <svg className="w-3.5 h-3.5 text-green-500 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
            </svg>
            Answered: {selectedAnswer}
          </div>
        ) : (
          <>
            {follow_up_options?.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mb-2.5">
                {follow_up_options.map((opt, i) => (
                  <button
                    key={i}
                    onClick={() => pick(opt)}
                    disabled={isLoading}
                    className="px-2.5 py-1 text-xs bg-blue-50 border border-blue-300 text-blue-700 rounded-lg hover:bg-blue-100 disabled:opacity-50 transition-colors"
                  >
                    {opt}
                  </button>
                ))}
              </div>
            )}
            <form onSubmit={submit} className="flex gap-1.5">
              <input
                type="text"
                value={custom}
                onChange={(e) => setCustom(e.target.value)}
                disabled={isLoading}
                placeholder="Or type your own answer…"
                className="flex-1 text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 bg-gray-50 focus:outline-none focus:border-blue-400 focus:bg-white placeholder-gray-400"
              />
              <button
                type="submit"
                disabled={!custom.trim() || isLoading}
                className="px-3 py-1.5 text-xs bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 font-medium transition-colors"
              >
                Send
              </button>
            </form>
          </>
        )}

        <span className="text-[11px] text-gray-400 mt-2 block">{fmtTime(ts)}</span>
      </div>
    </div>
  )
}

// ─── Assistant: error bubble ──────────────────────────────────────────────────

const ERR_META = {
  SESSION_EXPIRED: { icon: '⏰', label: 'Session Expired' },
  ACCESS_DENIED:   { icon: '🔒', label: 'Access Denied'  },
  SCHEMA_CHANGED:  { icon: '🔄', label: 'Schema Changed' },
}

function ErrorBubble({ msg }) {
  const { content, errorCode, ts } = msg
  const meta = ERR_META[errorCode] || { icon: '⚠️', label: 'Error' }

  return (
    <div className="flex items-start gap-2 mb-4 px-3">
      <BotAvatar />
      <div className="flex-1 min-w-0 bg-red-50 border border-red-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm">
        <div className="flex items-center gap-1.5 mb-1">
          <span className="text-base leading-none">{meta.icon}</span>
          <span className="text-xs font-semibold text-red-700">{meta.label}</span>
        </div>
        <p className="text-sm text-red-600 leading-relaxed">{content}</p>
        <span className="text-[11px] text-red-400 mt-2 block">{fmtTime(ts)}</span>
      </div>
    </div>
  )
}

// ─── Empty state ──────────────────────────────────────────────────────────────

function EmptyChatState({ onPrompt }) {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-5 pb-6">
      <div className="w-12 h-12 rounded-2xl bg-blue-50 flex items-center justify-center mb-3">
        <svg className="w-6 h-6 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
        </svg>
      </div>
      <p className="text-sm font-semibold text-gray-600 mb-1">Start a conversation</p>
      <p className="text-xs text-gray-400 leading-relaxed mb-5">
        Ask anything about your KRA data.
      </p>
      <div className="flex flex-col gap-2 w-full max-w-[240px]">
        {QUICK_PROMPTS.map((q) => (
          <button
            key={q}
            onClick={() => onPrompt(q)}
            className="text-xs px-3.5 py-2 rounded-xl border border-gray-200 text-gray-500 hover:border-blue-300 hover:text-blue-600 hover:bg-blue-50 transition-colors text-left"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  )
}

// ─── Main ChatPanel ───────────────────────────────────────────────────────────

export default function ChatPanel() {
  const [minimized, setMinimized]   = useState(false)
  const [query, setQuery]           = useState('')
  const [panelWidth, setPanelWidth] = useState(DEFAULT_WIDTH)

  const {
    isLoading,
    chatHistory,
    generateReport,
    clarifyReport,
    clearChat,
    clearFilters,
  } = useStore()

  const textareaRef = useRef(null)
  const chatEndRef  = useRef(null)
  const isDragging  = useRef(false)
  const dragStartX  = useRef(0)
  const dragStartW  = useRef(0)

  // Auto-scroll on new messages
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chatHistory])

  // ── Drag-to-resize ─────────────────────────────────────────────────────────
  const onDragStart = useCallback((e) => {
    isDragging.current = true
    dragStartX.current = e.clientX
    dragStartW.current = panelWidth
    document.body.style.cursor     = 'col-resize'
    document.body.style.userSelect = 'none'
    e.preventDefault()
  }, [panelWidth])

  useEffect(() => {
    const onMove = (e) => {
      if (!isDragging.current) return
      const delta = dragStartX.current - e.clientX
      setPanelWidth(Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, dragStartW.current + delta)))
    }
    const onUp = () => {
      if (!isDragging.current) return
      isDragging.current = false
      document.body.style.cursor     = ''
      document.body.style.userSelect = ''
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    return () => { document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp) }
  }, [])

  // ── Handlers ───────────────────────────────────────────────────────────────
  const handleSubmit = async (e) => {
    e?.preventDefault()
    const trimmed = query.trim()
    if (!trimmed || isLoading) return
    clearFilters()
    setQuery('')
    generateReport(trimmed)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSubmit(e) }
  }

  const handlePrompt = (q) => {
    clearFilters()
    generateReport(q)
  }

  // ── Minimized strip ────────────────────────────────────────────────────────
  if (minimized) {
    return (
      <div className="flex flex-col items-center pt-3 w-10 border-l border-gray-200 bg-white flex-shrink-0">
        {chatHistory.length > 0 && (
          <div className="w-2 h-2 rounded-full bg-blue-500 mb-1.5" />
        )}
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
  const userMsgCount = chatHistory.filter((m) => m.role === 'user').length

  return (
    <div
      className="relative flex flex-col flex-shrink-0 border-l border-gray-200 bg-gray-50"
      style={{ width: panelWidth }}
    >
      {/* Drag handle */}
      <div
        onMouseDown={onDragStart}
        title="Drag to resize"
        className="absolute left-0 top-0 bottom-0 w-1.5 cursor-col-resize z-10 group"
      >
        <div className="absolute inset-y-0 left-0 w-0.5 bg-transparent group-hover:bg-blue-400 transition-colors" />
      </div>

      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 bg-white flex-shrink-0">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-md bg-blue-600 flex items-center justify-center">
            <svg className="w-3.5 h-3.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          </div>
          <span className="text-sm font-semibold text-gray-800">KRA Assistant</span>
          {userMsgCount > 0 && (
            <span className="text-xs text-gray-400">{userMsgCount} {userMsgCount === 1 ? 'query' : 'queries'}</span>
          )}
        </div>

        <div className="flex items-center gap-1">
          {chatHistory.length > 0 && (
            <button
              onClick={clearChat}
              title="Clear chat"
              className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-md transition-colors"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
            </button>
          )}
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
      </div>

      {/* Chat messages */}
      <div className="flex-1 overflow-y-auto py-4">
        {chatHistory.length === 0 && <EmptyChatState onPrompt={handlePrompt} />}

        {chatHistory.map((msg) => {
          if (msg.role === 'user') return <UserBubble key={msg.id} msg={msg} />
          if (msg.role === 'assistant') {
            if (msg.type === 'thinking')      return <ThinkingBubble key={msg.id} />
            if (msg.type === 'report')        return <ReportBubble key={msg.id} msg={msg} />
            if (msg.type === 'message')       return <TextBubble key={msg.id} msg={msg} onSuggestion={handlePrompt} />
            if (msg.type === 'clarification') return <ClarificationBubble key={msg.id} msg={msg} clarifyReport={clarifyReport} isLoading={isLoading} />
            if (msg.type === 'error')         return <ErrorBubble key={msg.id} msg={msg} />
          }
          return null
        })}

        <div ref={chatEndRef} />
      </div>

      {/* Input bar */}
      <div className="border-t border-gray-200 bg-white px-3 pb-3 pt-2.5 flex-shrink-0">
        <form onSubmit={handleSubmit} className="flex items-end gap-2">
          <textarea
            ref={textareaRef}
            rows={2}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isLoading}
            placeholder="Ask a question… (Enter to send, Shift+Enter for newline)"
            className="flex-1 resize-none rounded-xl border border-gray-300 px-3 py-2 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 disabled:bg-gray-50 disabled:text-gray-400"
          />

          {/* Send + Clear stacked */}
          <div className="flex flex-col gap-1.5 flex-shrink-0">
            <button
              type="submit"
              disabled={!query.trim() || isLoading}
              title="Send"
              className="flex items-center justify-center w-9 h-9 bg-blue-600 text-white rounded-xl hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {isLoading ? (
                <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
              ) : (
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                </svg>
              )}
            </button>

            {(chatHistory.length > 0 || query) && (
              <button
                type="button"
                onClick={() => { setQuery(''); clearChat() }}
                title="Clear chat"
                className="flex items-center justify-center w-9 h-7 text-xs text-gray-400 border border-gray-200 rounded-lg hover:bg-red-50 hover:text-red-500 hover:border-red-200 transition-colors"
              >
                Clear
              </button>
            )}
          </div>
        </form>
        <p className="mt-1.5 text-[11px] text-gray-400 text-center">
          GPT-4o-mini · MySQL · KRA data
        </p>
      </div>
    </div>
  )
}
