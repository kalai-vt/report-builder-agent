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
        <p className="text-sm font-medium text-gray-600">Building report…</p>
        <p className="text-xs text-gray-400 mt-1">Executing SQL against the database</p>
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
  const { currentPrompt, error, errorCode } = useStore()

  let iconBg, iconColor, iconPath, title, body
  if (!error) {
    iconBg = 'bg-amber-50'; iconColor = 'text-amber-400'
    iconPath = 'M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z'
    title = 'No Results Found'
    body = 'Query executed successfully, but returned no matching records. Try adjusting your filters or broadening your search.'
  } else if (errorCode === 'DB_ERROR') {
    iconBg = 'bg-red-50'; iconColor = 'text-red-400'
    iconPath = 'M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375m16.5 5.625c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125'
    title = 'Database Connection Error'
    body = 'A database connection or execution error occurred. Please try again. If the issue persists, contact your system administrator.'
  } else {
    iconBg = 'bg-orange-50'; iconColor = 'text-orange-400'
    iconPath = 'M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z'
    title = 'System Failed Error'
    body = 'An unexpected system error occurred while processing your request. Please try again or rephrase your query.'
  }

  return (
    <div className="flex flex-col items-center justify-center h-full px-8 text-center">
      <div className={`w-14 h-14 rounded-2xl ${iconBg} flex items-center justify-center mb-3`}>
        <svg className={`w-7 h-7 ${iconColor}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d={iconPath} />
        </svg>
      </div>
      <h2 className="text-base font-semibold text-gray-700 mb-1">{title}</h2>
      <p className="text-sm text-gray-400 max-w-xs">{body}</p>
      {currentPrompt && (
        <p className="mt-3 text-xs text-gray-400 italic max-w-xs truncate">
          Query: "{currentPrompt}"
        </p>
      )}
    </div>
  )
}

function RestrictedOperation({ message }) {
  return (
    <div className="flex flex-col items-center justify-center h-full px-8 text-center">
      <div className="w-14 h-14 rounded-2xl bg-red-50 flex items-center justify-center mb-3">
        <svg className="w-7 h-7 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
        </svg>
      </div>
      <h2 className="text-base font-semibold text-gray-700 mb-2">Operation Restricted</h2>
      <p className="text-sm text-gray-500 max-w-sm leading-relaxed">
        {message ||
          'AI Report Builder supports read-only reporting and analytics. ' +
          'Operations such as create, update, delete, modify, assign, or reassign are not supported.'}
      </p>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function ReportBuilderPage() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const { status, hasData, isLoading, sessionId, refreshReport, message } = useStore()
  const didAutoRefresh = useRef(false)

  useEffect(() => {
    if (sessionId && !hasData && !isLoading && !didAutoRefresh.current) {
      didAutoRefresh.current = true
      refreshReport()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const showRestricted = !isLoading && status === 'restricted_operation'
  const showEmpty      = !isLoading && !hasData && !status
  const showNoResults  = !isLoading && !hasData && status === 'success'
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

            {/* Restricted write/action operation */}
            {showRestricted && <RestrictedOperation message={message} />}

            {/* Initial empty / chat-handled state */}
            {!showRestricted && (showEmpty || (showWorkspaceBlank && !hasData)) && !isLoading && <EmptyState />}

            {/* Loading — no prior data */}
            {isLoading && !hasData && <LoadingOverlay />}

            {/* Query ran, 0 rows or SQL error */}
            {!showRestricted && showNoResults && (
              <>
                <SqlPanel />
                <NoResults />
              </>
            )}

            {/* Report data (suppressed while a restriction notice is active) */}
            {hasData && !showRestricted && (
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
