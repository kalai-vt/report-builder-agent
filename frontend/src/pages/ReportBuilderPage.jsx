import { useState, useEffect, useRef } from 'react'
import Header from '../components/Header'
import ReportTable from '../components/ReportTable'
import ExportButtons from '../components/ExportButtons'
import SavedReportsSidebar from '../components/SavedReportsSidebar'
import RefreshStatusBadge from '../components/RefreshStatusBadge'
import SqlPanel from '../components/SqlPanel'
import ChatPanel from '../components/ChatPanel'
import useStore from '../store/useStore'

// ─── Left workspace states ────────────────────────────────────────────────────

function LoadingOverlay() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4">
      <div className="relative">
        <div className="w-12 h-12 border-4 border-blue-100 rounded-full" />
        <div className="absolute inset-0 w-12 h-12 border-4 border-blue-600 border-t-transparent rounded-full animate-spin" />
      </div>
      <div className="text-center">
        <p className="text-sm font-medium text-gray-600">Generating report…</p>
        <p className="text-xs text-gray-400 mt-1">AI is building and executing your SQL query</p>
      </div>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center h-full px-8 text-center">
      <div className="w-16 h-16 rounded-2xl bg-blue-50 flex items-center justify-center mb-4">
        <svg className="w-8 h-8 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
      </div>
      <h2 className="text-base font-semibold text-gray-700 mb-1">Ask anything about your KRA data</h2>
      <p className="text-sm text-gray-400 max-w-sm">
        Type a natural language question below. The AI will generate SQL, run it, and display the results here.
      </p>
      <div className="mt-6 grid grid-cols-2 gap-2 max-w-sm text-left">
        {[
          'Show all active KRAs for this quarter',
          'Employee productivity last month',
          'Top 10 employees by completion rate',
          'KRAs pending manager approval',
        ].map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => useStore.getState().generateReport(q)}
            className="text-xs p-2.5 border border-gray-200 rounded-lg text-gray-500 hover:border-blue-300 hover:text-blue-600 hover:bg-blue-50 transition-colors text-left"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  )
}

function NoResults() {
  const { currentPrompt, error } = useStore()
  return (
    <div className="flex flex-col items-center justify-center h-full px-8 text-center">
      <div className="w-14 h-14 rounded-2xl bg-amber-50 flex items-center justify-center mb-3">
        <svg className="w-7 h-7 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
        </svg>
      </div>
      <h2 className="text-base font-semibold text-gray-700 mb-1">
        {error ? 'Query error' : 'No results found'}
      </h2>
      <p className="text-sm text-gray-400 max-w-xs">
        {error
          ? 'The SQL query encountered an error. See the SQL panel below or try rephrasing your query.'
          : 'The query ran successfully but returned no matching records. Try adjusting your filters or query.'}
      </p>
      {currentPrompt && (
        <p className="mt-3 text-xs text-gray-400 italic max-w-xs truncate">
          Query: "{currentPrompt}"
        </p>
      )}
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function ReportBuilderPage() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const { status, hasData, isLoading, sessionId, refreshReport } = useStore()
  const didAutoRefresh = useRef(false)

  useEffect(() => {
    if (sessionId && !hasData && !isLoading && !didAutoRefresh.current) {
      didAutoRefresh.current = true
      refreshReport()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const showEmpty     = !isLoading && !hasData && !status
  const showNoResults = !isLoading && !hasData && status === 'success'
  // For chat-handled statuses, keep the left workspace as the empty state
  const showWorkspaceBlank = ['greeting', 'off_topic', 'filter_redirect', 'clarification_needed'].includes(status)

  return (
    <div className="flex flex-col h-screen bg-gray-50 overflow-hidden">
      <Header onToggleSidebar={() => setSidebarOpen((o) => !o)} />
      <SavedReportsSidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      {/* ── Split workspace ─────────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── Left: report workspace ──────────────────────────────────────── */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="flex-1 overflow-y-auto scrollbar-thin px-5 py-4">

            {/* Initial empty / chat-handled state */}
            {(showEmpty || (showWorkspaceBlank && !hasData)) && !isLoading && <EmptyState />}

            {/* Loading — no prior data */}
            {isLoading && !hasData && <LoadingOverlay />}

            {/* Query ran, 0 rows or SQL error */}
            {showNoResults && (
              <>
                <SqlPanel />
                <NoResults />
              </>
            )}

            {/* Report data */}
            {hasData && (
              <>
                <RefreshStatusBadge />
                <SqlPanel />
                <div className="flex justify-end mb-2">
                  <ExportButtons />
                </div>
                <ReportTable />
              </>
            )}

            {/* Refreshing spinner (data already visible) */}
            {isLoading && hasData && (
              <div className="fixed bottom-6 left-1/3 -translate-x-1/2 bg-white border border-gray-200 rounded-full shadow-md px-3 py-2 flex items-center gap-2 text-xs text-gray-600 z-10">
                <span className="w-3 h-3 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                Refreshing…
              </div>
            )}
          </div>
        </div>

        {/* ── Right: chatbot panel ─────────────────────────────────────────── */}
        <ChatPanel />
      </div>
    </div>
  )
}
