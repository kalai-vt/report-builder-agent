import { useNavigate } from 'react-router-dom'
import Header from '../components/Header'
import useStore from '../store/useStore'

function timeAgo(iso) {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  return d === 1 ? '1 day ago' : `${d} days ago`
}

export default function SavedReportsPage() {
  const navigate = useNavigate()
  const { savedReports, loadSavedReport, deleteSavedReport } = useStore()

  const handleLoad = async (saved) => {
    navigate('/')
    await loadSavedReport(saved)
  }

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      <Header onToggleSidebar={() => {}} />

      <main className="flex-1 overflow-y-auto scrollbar-thin">
        <div className="max-w-4xl mx-auto px-4 py-8">
          {/* Page header */}
          <div className="flex items-center justify-between mb-6">
            <div>
              <h1 className="text-xl font-bold text-gray-800">Saved Reports</h1>
              <p className="text-sm text-gray-500 mt-0.5">
                {savedReports.length} saved report{savedReports.length !== 1 ? 's' : ''} · Reload fetches latest data from DB
              </p>
            </div>
            <button
              onClick={() => navigate('/')}
              className="flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-800 hover:underline"
            >
              ← Back to Builder
            </button>
          </div>

          {/* Empty state */}
          {savedReports.length === 0 && (
            <div className="flex flex-col items-center justify-center py-24 text-center">
              <div className="w-16 h-16 rounded-2xl bg-gray-100 flex items-center justify-center mb-4">
                <svg className="w-8 h-8 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                    d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
                </svg>
              </div>
              <p className="text-sm font-medium text-gray-500">No saved reports</p>
              <p className="text-xs text-gray-400 mt-1 max-w-xs">
                Run a query in the Report Builder and click "Save Report" to save it here.
              </p>
              <button
                onClick={() => navigate('/')}
                className="mt-4 px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 transition-colors"
              >
                Go to Report Builder
              </button>
            </div>
          )}

          {/* Report cards */}
          {savedReports.length > 0 && (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {savedReports.map((report) => (
                <div
                  key={report.id}
                  className="bg-white border border-gray-200 rounded-xl p-4 hover:border-blue-300 hover:shadow-sm transition-all group"
                >
                  {/* Card header */}
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div className="w-8 h-8 rounded-lg bg-blue-50 flex items-center justify-center flex-shrink-0">
                      <svg className="w-4 h-4 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                          d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                      </svg>
                    </div>
                    <button
                      onClick={() => deleteSavedReport(report.id)}
                      className="opacity-0 group-hover:opacity-100 text-gray-300 hover:text-red-400 transition-all text-lg leading-none"
                      title="Delete report"
                    >
                      ×
                    </button>
                  </div>

                  {/* Name */}
                  <h3 className="text-sm font-semibold text-gray-800 mb-1 line-clamp-2" title={report.name}>
                    {report.name}
                  </h3>

                  {/* Prompt */}
                  <p className="text-xs text-gray-400 line-clamp-2 mb-3" title={report.prompt}>
                    {report.prompt}
                  </p>

                  {/* Meta */}
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-gray-300">{timeAgo(report.savedAt)}</span>
                    <span className="text-xs text-gray-300 font-mono truncate max-w-[80px]" title={report.sessionId}>
                      {report.sessionId?.slice(0, 10)}…
                    </span>
                  </div>

                  {/* Load button */}
                  <button
                    onClick={() => handleLoad(report)}
                    className="mt-3 w-full py-2 text-xs bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-medium"
                  >
                    Load Report
                  </button>

                  <p className="text-center text-xs text-gray-300 mt-1.5">
                    Fetches live data · no LLM call
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  )
}
