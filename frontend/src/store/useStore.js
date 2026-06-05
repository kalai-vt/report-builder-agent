import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import * as api from '../api/client'

// Detect follow-up / continuation phrases so we can enrich them with previous context
const _CONTINUATION_RE = /^\s*(add|include|also\s+show|also\s+include|also\s+add|also\s+display|remove|exclude|don'?t\s+show|hide|filter\s+(by|for|on)|sort\s+by|order\s+by|group\s+by|only\s+show|show\s+only|just\s+show|with\s+\w|and\s+also|now\s+(show|add|include|filter|sort|group)|update\s+the\s+report|change\s+the\s+report|narrow\s+(down|to|by)|break\s+down\s+by|summarize\s+by|count\s+by)\b/i

function _isContinuation(query) {
  return _CONTINUATION_RE.test(query)
}

// Build an enriched query that accumulates context across multiple follow-ups.
// Uses the last user message's apiQuery (full enriched query) as the base,
// so multi-turn follow-ups build on the complete accumulated context, not just
// the most recent short display message.
function _enrichFollowUp(query, chatHistory) {
  const lastUser = [...chatHistory].reverse().find((m) => m.role === 'user' && m.type === 'message')
  if (!lastUser) return query
  // Prefer apiQuery (full accumulated context) over content (short display text)
  const base = lastUser.apiQuery || lastUser.content
  return `${base}. Also: ${query}`
}

const INITIAL_REPORT = {
  status: null,
  reportData: [],
  filteredData: [],
  columns: [],
  explanation: '',
  sqlQuery: '',
  rowCount: 0,
  executionTime: 0,
  cacheHit: false,
  filterableColumns: [],
  dimensions: [],
  recommendedFilters: [],
  page: 1,
  pageSize: 1000,
  totalRows: 0,
  totalPages: 1,
  hasNextPage: false,
  hasPrevPage: false,
  clarification: null,
  refreshMode: false,
  refreshedAt: null,
  error: null,
  errorCode: null,
  message: null,
  suggestions: null,
  hasData: false,
  activeFilters: {},
}

let _uid = 0
const _uuid = () => `m${Date.now()}${++_uid}`
const _now  = () => new Date().toISOString()

const useStore = create(
  persist(
    (set, get) => ({
      // ── Persisted prefs ──────────────────────────────────────────────────────
      userId: 'demo_user',
      userRole: 'employee',
      sessionId: null,
      currentPrompt: '',
      savedReports: [],

      // ── Ephemeral report state ───────────────────────────────────────────────
      ...INITIAL_REPORT,

      // ── Chat history (not persisted) ─────────────────────────────────────────
      chatHistory: [],
      pendingThinkingId: null,

      // ── UI ──────────────────────────────────────────────────────────────────
      isLoading: false,

      // ── User prefs ───────────────────────────────────────────────────────────
      setUserId:   (id)   => set({ userId: id }),
      setUserRole: (role) => set({ userRole: role }),

      // ── Generate ─────────────────────────────────────────────────────────────
      generateReport: async (query) => {
        const { userId, userRole, hasData, chatHistory } = get()
        const thinkingId = _uuid()

        // Enrich follow-up queries with the previous user prompt so the backend
        // has full context for both intent classification and SQL generation.
        const apiQuery = (hasData && _isContinuation(query))
          ? _enrichFollowUp(query, chatHistory)
          : query

        set({
          isLoading: true,
          error: null, errorCode: null,
          message: null, clarification: null, suggestions: null,
          pendingThinkingId: thinkingId,
          chatHistory: [
            ...chatHistory,
            // content = short display text shown in the bubble
            // apiQuery = full enriched query sent to backend (used as base for next follow-up)
            { id: _uuid(), role: 'user', type: 'message', content: query, apiQuery: apiQuery, ts: _now() },
            { id: thinkingId, role: 'assistant', type: 'thinking', ts: _now() },
          ],
        })

        try {
          const res = await api.generateReport({
            query: apiQuery, user_id: userId, user_role: userRole,
            page: 1, page_size: 0, has_data: hasData,
          })
          get()._handleResponse(res.data, query)   // keep original for display/history
        } catch (err) {
          get()._handleError(err)
        } finally {
          set({ isLoading: false, pendingThinkingId: null })
        }
      },

      // ── Clarify ──────────────────────────────────────────────────────────────
      clarifyReport: async (answer) => {
        const { sessionId, userId, userRole, chatHistory } = get()
        const thinkingId = _uuid()

        const updatedHistory = chatHistory.map((m) =>
          m.type === 'clarification' && !m.answered
            ? { ...m, answered: true, selectedAnswer: answer }
            : m
        )

        set({
          isLoading: true,
          error: null, errorCode: null,
          pendingThinkingId: thinkingId,
          chatHistory: [
            ...updatedHistory,
            { id: _uuid(), role: 'user',      type: 'message',  content: answer, ts: _now() },
            { id: thinkingId, role: 'assistant', type: 'thinking', ts: _now() },
          ],
        })

        try {
          const res = await api.clarifyReport({
            session_id: sessionId, user_answer: answer,
            user_id: userId, user_role: userRole,
          })
          get()._handleResponse(res.data, null)
        } catch (err) {
          get()._handleError(err)
        } finally {
          set({ isLoading: false, pendingThinkingId: null })
        }
      },

      // ── Refresh (no chatHistory changes) ─────────────────────────────────────
      refreshReport: async () => {
        const { sessionId, userId, userRole, page, pageSize } = get()
        if (!sessionId) return
        set({ isLoading: true, error: null })
        try {
          const res = await api.refreshReport(sessionId, userId, userRole, page, pageSize)
          const data = res.data
          if (data.status === 'success') {
            const rows = data.data || []
            const cols = rows.length > 0 ? Object.keys(rows[0]) : get().columns
            set({
              status: 'success',
              reportData: rows, filteredData: rows, columns: cols,
              rowCount: data.row_count ?? rows.length,
              totalRows: data.total_rows ?? rows.length,
              refreshMode: data.refresh_mode ?? true,
              refreshedAt: data.refreshed_at || new Date().toISOString(),
              hasData: rows.length > 0,
              activeFilters: {},
            })
          } else if (data.error_code) {
            set({ error: data.message || 'Refresh failed', errorCode: data.error_code })
          }
        } catch (err) {
          set({ error: err.message || 'Refresh failed' })
        } finally {
          set({ isLoading: false })
        }
      },

      // ── Page navigation (no chatHistory changes) ──────────────────────────────
      loadPage: async (pageNum) => {
        const { sessionId, userId, pageSize } = get()
        if (!sessionId) return
        set({ isLoading: true })
        try {
          const res = await api.getReportPage(sessionId, userId, pageNum, pageSize)
          const d = res.data
          const rows = d.data || []
          const cols = rows.length > 0 ? Object.keys(rows[0]) : get().columns
          set({
            reportData: rows, filteredData: rows, columns: cols,
            page: d.page || pageNum,
            totalRows: d.total_rows || 0,
            totalPages: d.total_pages || 1,
            hasNextPage: d.has_next_page || false,
            hasPrevPage: d.has_prev_page || false,
            activeFilters: {},
          })
        } catch (err) {
          set({ error: err.message || 'Page load failed' })
        } finally {
          set({ isLoading: false })
        }
      },

      // ── Client-side filters ───────────────────────────────────────────────────
      applyFilters: (filters) => {
        const { reportData } = get()
        let filtered = [...reportData]

        Object.entries(filters).forEach(([col, filter]) => {
          if (!filter) return
          const { type, value } = filter
          if (value === null || value === undefined || value === '') return
          if (Array.isArray(value) && value.length === 0) return

          filtered = filtered.filter((row) => {
            const cell = row[col]
            if (type === 'text_search' || type === 'text')
              return String(cell ?? '').toLowerCase().includes(String(value).toLowerCase())
            if (type === 'categorical') {
              if (Array.isArray(value)) return value.length === 0 || value.includes(String(cell ?? ''))
              return String(cell ?? '') === String(value)
            }
            if (type === 'numeric_range') {
              const num = parseFloat(cell)
              if (isNaN(num)) return false
              const { min, max } = value || {}
              if (min !== '' && min !== null && min !== undefined && !isNaN(+min) && num < +min) return false
              if (max !== '' && max !== null && max !== undefined && !isNaN(+max) && num > +max) return false
              return true
            }
            if (type === 'date_range') {
              const dt = new Date(cell)
              const { from, to } = value || {}
              if (from && dt < new Date(from)) return false
              if (to && dt > new Date(to + 'T23:59:59')) return false
              return true
            }
            return true
          })
        })

        set({ filteredData: filtered, activeFilters: filters })
      },

      clearFilters: () => set({ filteredData: get().reportData, activeFilters: {} }),

      // ── Saved reports ─────────────────────────────────────────────────────────
      saveReport: (name) => {
        const { sessionId, currentPrompt, sqlQuery, explanation, filterableColumns, savedReports } = get()
        if (!sessionId || !sqlQuery) return
        const trimmedName = (name || currentPrompt).trim().slice(0, 80)
        const existing = savedReports.find((r) => r.sessionId === sessionId)
        if (existing) {
          set({ savedReports: savedReports.map((r) => r.sessionId === sessionId ? { ...r, name: trimmedName } : r) })
          return
        }
        set({
          savedReports: [
            { id: String(Date.now()), name: trimmedName, prompt: currentPrompt, sqlQuery, explanation, filterableColumns, savedAt: new Date().toISOString() },
            ...savedReports,
          ],
        })
      },

      loadSavedReport: async (saved) => {
        const { userId, userRole } = get()

        // Clear ALL stale report data + chat before loading — no static data shown
        set({
          chatHistory: [],
          pendingThinkingId: null,
          ...INITIAL_REPORT,
          currentPrompt: saved.prompt,
          isLoading: true,
        })

        if (!saved.sqlQuery) {
          set({ isLoading: false, status: 'error', error: 'This saved report has no stored SQL and cannot be replayed. Please regenerate it.' })
          return
        }

        try {
          const res = await api.replayReport({
            sql_query: saved.sqlQuery,
            user_id: userId,
            user_role: userRole,
            page: 1,
            page_size: 0,
          })
          const data = res.data

          if (data.status === 'error' || data.error_code) {
            set({ status: 'error', error: data.error || 'Failed to load saved report.', errorCode: data.error_code || null })
            return
          }

          const rows = data.data || []
          const cols = rows.length > 0 ? Object.keys(rows[0]) : []
          set({
            status: 'success',
            sessionId: data.session_id,
            reportData: rows,
            filteredData: rows,
            columns: cols,
            explanation: data.explanation || '',
            sqlQuery: data.sql_query || saved.sqlQuery,
            rowCount: data.row_count ?? rows.length,
            executionTime: data.execution_time || 0,
            cacheHit: false,
            filterableColumns: data.filterable_columns || [],
            dimensions: data.dimensions || [],
            recommendedFilters: data.recommended_column_filters || [],
            page: data.page || 1,
            pageSize: data.page_size || 1000,
            totalRows: data.total_rows ?? rows.length,
            totalPages: data.total_pages || 1,
            hasNextPage: data.has_next_page || false,
            hasPrevPage: data.has_prev_page || false,
            refreshMode: true,
            refreshedAt: data.refreshed_at || new Date().toISOString(),
            hasData: rows.length > 0,
            activeFilters: {},
            error: null,
          })
        } catch (err) {
          const detail = err.response?.data?.detail || err.message || 'Failed to load saved report.'
          set({ status: 'error', error: detail, errorCode: null })
        } finally {
          set({ isLoading: false })
        }
      },

      deleteSavedReport: (id) => set({ savedReports: get().savedReports.filter((r) => r.id !== id) }),

      // ── WebSocket stream ──────────────────────────────────────────────────────
      applyStreamData: (data) => {
        if (data.error_code) { set({ error: data.message || data.error_code }); return }
        const rows = data.data || []
        const cols = rows.length > 0 ? Object.keys(rows[0]) : get().columns
        set({ status: 'success', reportData: rows, filteredData: rows, columns: cols, rowCount: data.row_count || rows.length, refreshMode: true, refreshedAt: data.refreshed_at || new Date().toISOString(), activeFilters: {}, hasData: rows.length > 0 })
      },

      // ── UI helpers ────────────────────────────────────────────────────────────
      clearError:   () => set({ error: null, errorCode: null }),
      clearMessage: () => set({ message: null, suggestions: null }),
      clearChat:    () => set({ chatHistory: [], pendingThinkingId: null, ...INITIAL_REPORT, sessionId: null, currentPrompt: '' }),
      reset:        () => set({ ...INITIAL_REPORT, sessionId: null, currentPrompt: '' }),

      // ── Private: response handler ─────────────────────────────────────────────
      _handleResponse: (data, prompt) => {
        const { chatHistory, pendingThinkingId } = get()
        const status = data.status
        const msgId  = pendingThinkingId || _uuid()

        const replaceThinking = (newMsg) =>
          pendingThinkingId
            ? chatHistory.map((m) => (m.id === pendingThinkingId ? newMsg : m))
            : [...chatHistory, newMsg]

        if (['greeting', 'off_topic', 'filter_redirect'].includes(status)) {
          set({
            status, message: data.message || '', suggestions: data.suggestions || null,
            sessionId: data.session_id,
            chatHistory: replaceThinking({
              id: msgId, role: 'assistant', type: 'message',
              content: data.message || '', suggestions: data.suggestions || [],
              status, ts: _now(),
            }),
          })
          return
        }

        if (status === 'clarification_needed') {
          const clar = {
            follow_up_question: data.follow_up_question || '',
            follow_up_options: data.follow_up_options || [],
            clarification_round: data.clarification_round || 0,
            original_prompt: data.original_prompt || '',
          }
          set({
            status, sessionId: data.session_id,
            clarification: clar, message: null,
            chatHistory: replaceThinking({
              id: msgId, role: 'assistant', type: 'clarification',
              clarification: clar, answered: false, ts: _now(),
            }),
          })
          return
        }

        if (status === 'success') {
          const rows = data.data || []
          const cols = rows.length > 0 ? Object.keys(rows[0]) : []
          const totalRows = data.total_rows ?? data.row_count ?? rows.length

          set({
            status: 'success',
            sessionId: data.session_id,
            currentPrompt: prompt !== null ? prompt || get().currentPrompt : get().currentPrompt,
            reportData: rows, filteredData: rows, columns: cols,
            explanation: data.explanation || '',
            sqlQuery: data.sql_query || '',
            rowCount: data.row_count ?? rows.length,
            executionTime: data.execution_time || 0,
            cacheHit: data.cache_hit || false,
            filterableColumns: data.filterable_columns || [],
            dimensions: data.dimensions || [],
            recommendedFilters: data.recommended_column_filters || [],
            page: data.page || 1,
            pageSize: data.page_size || 1000,
            totalRows,
            totalPages: data.total_pages || 1,
            hasNextPage: data.has_next_page || false,
            hasPrevPage: data.has_prev_page || false,
            refreshMode: data.refresh_mode || false,
            refreshedAt: data.refreshed_at || null,
            error: data.error || null,
            hasData: rows.length > 0,
            clarification: null, message: null, suggestions: null,
            activeFilters: {},
            chatHistory: replaceThinking({
              id: msgId, role: 'assistant', type: 'report',
              explanation: data.explanation || '',
              snapshot: { rowCount: data.row_count ?? rows.length, totalRows },
              ts: _now(),
            }),
          })
          return
        }

        // error status
        set({
          status: 'error',
          error: data.error || 'An unknown error occurred.',
          errorCode: data.error_code || null,
          chatHistory: replaceThinking({
            id: msgId, role: 'assistant', type: 'error',
            content: data.error || 'An unknown error occurred.',
            errorCode: data.error_code || null, ts: _now(),
          }),
        })
      },

      // ── Private: error handler ────────────────────────────────────────────────
      _handleError: (err) => {
        const { chatHistory, pendingThinkingId } = get()
        const detail = err.response?.data?.detail || err.message || 'Unknown error'
        const msgId  = pendingThinkingId || _uuid()

        const replaceThinking = (newMsg) =>
          pendingThinkingId
            ? chatHistory.map((m) => (m.id === pendingThinkingId ? newMsg : m))
            : [...chatHistory, newMsg]

        let error, errorCode
        if (detail.includes('SESSION_EXPIRED')) {
          error = 'Your report session expired. Please regenerate the report.'; errorCode = 'SESSION_EXPIRED'
        } else if (detail.includes('ACCESS_DENIED')) {
          error = 'You do not have permission to access this report.'; errorCode = 'ACCESS_DENIED'
        } else if (detail.includes('SCHEMA_CHANGED')) {
          error = 'The database schema changed. Please regenerate the report.'; errorCode = 'SCHEMA_CHANGED'
        } else {
          error = detail; errorCode = null
        }

        set({
          error, errorCode: errorCode || null, clarification: null,
          chatHistory: replaceThinking({
            id: msgId, role: 'assistant', type: 'error',
            content: error, errorCode, ts: _now(),
          }),
        })
      },
    }),
    {
      name: 'ai-report-builder',
      partialize: (state) => ({
        userId: state.userId,
        userRole: state.userRole,
        sessionId: state.sessionId,
        currentPrompt: state.currentPrompt,
        savedReports: state.savedReports,
      }),
    }
  )
)

export default useStore
