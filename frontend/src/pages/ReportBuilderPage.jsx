import { useState, useEffect, useRef } from 'react'
import Header from '../components/Header'
import QueryInput from '../components/QueryInput'
import ReportTable from '../components/ReportTable'
import ExportButtons from '../components/ExportButtons'
import SavedReportsSidebar from '../components/SavedReportsSidebar'
import ClarificationPanel from '../components/ClarificationPanel'
import RefreshStatusBadge from '../components/RefreshStatusBadge'
import ErrorBanner from '../components/ErrorBanner'
import MessagePanel from '../components/MessagePanel'
import SqlPanel from '../components/SqlPanel'
import useStore from '../store/useStore'

function LoadingOverlay() {
  return (
    <div className="flex flex-col items-center justify-center py-20 gap-4">
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
    <div className="flex flex-col items-center justify-center py-24 px-4 text-center">
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

export default function ReportBuilderPage() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const { status, hasData, isLoading, sessionId, refreshReport } = useStore()
  const didAutoRefresh = useRef(false)

  // Auto-refresh on page reload when a sessionId is persisted
  useEffect(() => {
    if (sessionId && !hasData && !isLoading && !didAutoRefresh.current) {
      didAutoRefresh.current = true
      refreshReport()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const showEmpty = !isLoading && !hasData && status !== 'clarification_needed' &&
    !['greeting', 'off_topic', 'filter_redirect'].includes(status)

  return (
    <div className="flex flex-col h-screen bg-gray-50 overflow-hidden">
      <Header onToggleSidebar={() => setSidebarOpen((o) => !o)} />

      <SavedReportsSidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <div className="flex flex-col flex-1 overflow-hidden">
        {/* Scrollable report area */}
        <div className="flex-1 overflow-y-auto scrollbar-thin px-4 pt-4 pb-2">
          <ErrorBanner />
          <MessagePanel />
          <ClarificationPanel />

          {isLoading && !hasData && <LoadingOverlay />}
          {showEmpty && <EmptyState />}

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

          {/* Show refresh badge and loading spinner during refresh when data already exists */}
          {isLoading && hasData && (
            <div className="fixed bottom-36 right-6 bg-white border border-gray-200 rounded-full shadow-md px-3 py-2 flex items-center gap-2 text-xs text-gray-600">
              <span className="w-3 h-3 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
              Refreshing…
            </div>
          )}
        </div>

        {/* Fixed query input */}
        <QueryInput />
      </div>
    </div>
  )
}
