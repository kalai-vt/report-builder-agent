import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import * as api from '../api/client'

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

const useStore = create(
  persist(
    (set, get) => ({
      // Persisted user prefs
      userId: 'demo_user',
      userRole: 'employee',

      // Persisted session (for page-reload refresh)
      sessionId: null,
      currentPrompt: '',

      // Persisted saved reports
      savedReports: [],

      // Ephemeral report state
      ...INITIAL_REPORT,

      // UI
      isLoading: false,

      // ─── User prefs ─────────────────────────────────────────────────────────
      setUserId: (id) => set({ userId: id }),
      setUserRole: (role) => set({ userRole: role }),

      // ─── Generate ───────────────────────────────────────────────────────────
      generateReport: async (query) => {
        const { userId, userRole, hasData } = get()
        set({
          isLoading: true,
          error: null,
          errorCode: null,
          message: null,
          clarification: null,
          suggestions: null,
        })
        try {
          const res = await api.generateReport({
            query,
            user_id: userId,
            user_role: userRole,
            page: 1,
            page_size: 0,
            has_data: hasData,
          })
          get()._handleResponse(res.data, query)
        } catch (err) {
          get()._handleError(err)
        } finally {
          set({ isLoading: false })
        }
      },

      // ─── Clarify ────────────────────────────────────────────────────────────
      clarifyReport: async (answer) => {
        const { sessionId, userId, userRole } = get()
        set({ isLoading: true, error: null, errorCode: null })
        try {
          const res = await api.clarifyReport({
            session_id: sessionId,
            user_answer: answer,
            user_id: userId,
            user_role: userRole,
          })
          get()._handleResponse(res.data, null)
        } catch (err) {
          get()._handleError(err)
        } finally {
          set({ isLoading: false })
        }
      },

      // ─── Refresh ────────────────────────────────────────────────────────────
      refreshReport: async () => {
        const { sessionId, userId, userRole, page, pageSize } = get()
        if (!sessionId) return
        set({ isLoading: true, error: null })
        try {
          const res = await api.refreshReport(sessionId, userId, userRole, page, pageSize)
          get()._handleResponse(res.data, null)
        } catch (err) {
          get()._handleError(err)
        } finally {
          set({ isLoading: false })
        }
      },

      // ─── Page navigation ────────────────────────────────────────────────────
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
            reportData: rows,
            filteredData: rows,
            columns: cols,
            page: d.page || pageNum,
            totalRows: d.total_rows || 0,
            totalPages: d.total_pages || 1,
            hasNextPage: d.has_next_page || false,
            hasPrevPage: d.has_prev_page || false,
            activeFilters: {},
          })
        } catch (err) {
          get()._handleError(err)
        } finally {
          set({ isLoading: false })
        }
      },

      // ─── Client-side filters (NO API call, NO LLM) ──────────────────────────
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

            if (type === 'text_search' || type === 'text') {
              return String(cell ?? '').toLowerCase().includes(String(value).toLowerCase())
            }
            if (type === 'categorical') {
              if (Array.isArray(value)) {
                return value.length === 0 || value.includes(String(cell ?? ''))
              }
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

      // ─── Saved reports ──────────────────────────────────────────────────────
      saveReport: (name) => {
        const { sessionId, currentPrompt, savedReports } = get()
        if (!sessionId) return
        const trimmedName = (name || currentPrompt).trim().slice(0, 80)
        const existing = savedReports.find((r) => r.sessionId === sessionId)
        if (existing) {
          set({ savedReports: savedReports.map((r) => (r.sessionId === sessionId ? { ...r, name: trimmedName } : r)) })
          return
        }
        const newReport = {
          id: String(Date.now()),
          name: trimmedName,
          prompt: currentPrompt,
          sessionId,
          savedAt: new Date().toISOString(),
        }
        set({ savedReports: [newReport, ...savedReports] })
      },

      loadSavedReport: async (saved) => {
        const { userId, userRole } = get()
        set({
          sessionId: saved.sessionId,
          currentPrompt: saved.prompt,
          isLoading: true,
          error: null,
          activeFilters: {},
          clarification: null,
          message: null,
          suggestions: null,
        })
        try {
          const res = await api.refreshReport(saved.sessionId, userId, userRole, 1, 0)
          get()._handleResponse(res.data, null)
        } catch (err) {
          get()._handleError(err)
        } finally {
          set({ isLoading: false })
        }
      },

      deleteSavedReport: (id) => set({ savedReports: get().savedReports.filter((r) => r.id !== id) }),

      // ─── WebSocket stream ───────────────────────────────────────────────────
      applyStreamData: (data) => {
        if (data.error_code) {
          get()._handleError({ message: data.message || data.error_code })
          return
        }
        const rows = data.data || []
        const cols = rows.length > 0 ? Object.keys(rows[0]) : get().columns
        set({
          status: 'success',
          reportData: rows,
          filteredData: rows,
          columns: cols,
          rowCount: data.row_count || rows.length,
          refreshMode: true,
          refreshedAt: data.refreshed_at || new Date().toISOString(),
          activeFilters: {},
          hasData: rows.length > 0,
        })
      },

      // ─── UI helpers ─────────────────────────────────────────────────────────
      clearError: () => set({ error: null, errorCode: null }),
      clearMessage: () => set({ message: null, suggestions: null }),
      reset: () => set({ ...INITIAL_REPORT, sessionId: null, currentPrompt: '' }),

      // ─── Private response handler ───────────────────────────────────────────
      _handleResponse: (data, prompt) => {
        const status = data.status

        if (['greeting', 'off_topic', 'filter_redirect'].includes(status)) {
          set({
            status,
            message: data.message || '',
            suggestions: data.suggestions || null,
            sessionId: data.session_id,
          })
          return
        }

        if (status === 'clarification_needed') {
          set({
            status,
            sessionId: data.session_id,
            clarification: {
              follow_up_question: data.follow_up_question || '',
              follow_up_options: data.follow_up_options || [],
              clarification_round: data.clarification_round || 0,
              original_prompt: data.original_prompt || '',
            },
            message: null,
          })
          return
        }

        if (status === 'success') {
          const rows = data.data || []
          const cols = rows.length > 0 ? Object.keys(rows[0]) : []
          set({
            status: 'success',
            sessionId: data.session_id,
            currentPrompt: prompt !== null ? prompt || get().currentPrompt : get().currentPrompt,
            reportData: rows,
            filteredData: rows,
            columns: cols,
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
            totalRows: data.total_rows ?? data.row_count ?? rows.length,
            totalPages: data.total_pages || 1,
            hasNextPage: data.has_next_page || false,
            hasPrevPage: data.has_prev_page || false,
            refreshMode: data.refresh_mode || false,
            refreshedAt: data.refreshed_at || null,
            error: data.error || null,
            hasData: rows.length > 0,
            clarification: null,
            message: null,
            suggestions: null,
            activeFilters: {},
          })
          return
        }

        // error status
        set({
          status: 'error',
          error: data.error || 'An unknown error occurred.',
          errorCode: data.error_code || null,
        })
      },

      _handleError: (err) => {
        const detail = err.response?.data?.detail || err.message || 'Unknown error'
        if (detail.includes('SESSION_EXPIRED')) {
          set({ error: 'Your report session expired. Please regenerate the report.', errorCode: 'SESSION_EXPIRED', clarification: null })
        } else if (detail.includes('ACCESS_DENIED')) {
          set({ error: 'You do not have permission to access this report.', errorCode: 'ACCESS_DENIED', clarification: null })
        } else if (detail.includes('SCHEMA_CHANGED')) {
          set({ error: 'The database schema changed. Please regenerate the report.', errorCode: 'SCHEMA_CHANGED', clarification: null })
        } else {
          set({ error: detail, errorCode: null, clarification: null })
        }
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
