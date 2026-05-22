import { useNavigate } from 'react-router-dom'
import useStore from '../store/useStore'

function timeAgo(iso) {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

export default function SavedReportsSidebar({ open, onClose }) {
  const navigate = useNavigate()
  const { savedReports, loadSavedReport, deleteSavedReport } = useStore()

  const handleLoad = async (saved) => {
    onClose()
    navigate('/')
    await loadSavedReport(saved)
  }

  return (
    <>
      {/* Backdrop */}
      {open && (
        <div
          className="fixed inset-0 bg-black/20 z-20 lg:hidden"
          onClick={onClose}
        />
      )}

      {/* Sidebar panel */}
      <aside
        className={`fixed left-0 top-0 h-full z-30 bg-white border-r border-gray-200 shadow-lg flex flex-col transition-transform duration-200 w-72 ${
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
          <div className="flex items-center gap-2">
            <svg className="w-4 h-4 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
            </svg>
            <span className="font-semibold text-sm text-gray-800">Saved Reports</span>
            {savedReports.length > 0 && (
              <span className="text-xs px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded-full">
                {savedReports.length}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none"
          >
            ×
          </button>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto scrollbar-thin">
          {savedReports.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 px-4 text-center">
              <svg className="w-10 h-10 text-gray-200 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                  d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
              </svg>
              <p className="text-sm text-gray-400">No saved reports yet.</p>
              <p className="text-xs text-gray-300 mt-1">Run a query and click "Save Report"</p>
            </div>
          ) : (
            <ul className="py-2 divide-y divide-gray-50">
              {savedReports.map((report) => (
                <li key={report.id} className="group px-4 py-3 hover:bg-gray-50 transition-colors">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-800 truncate" title={report.name}>
                        {report.name}
                      </p>
                      <p className="text-xs text-gray-400 truncate mt-0.5" title={report.prompt}>
                        {report.prompt}
                      </p>
                      <p className="text-xs text-gray-300 mt-1">{timeAgo(report.savedAt)}</p>
                    </div>
                    <button
                      onClick={() => deleteSavedReport(report.id)}
                      className="opacity-0 group-hover:opacity-100 text-gray-300 hover:text-red-400 transition-all text-base leading-none flex-shrink-0 mt-0.5"
                      title="Delete"
                    >
                      ×
                    </button>
                  </div>
                  <button
                    onClick={() => handleLoad(report)}
                    className="mt-2 w-full text-xs px-3 py-1.5 bg-blue-50 text-blue-700 rounded-lg hover:bg-blue-100 transition-colors text-left"
                  >
                    Load Report →
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Footer link */}
        <div className="px-4 py-3 border-t border-gray-100">
          <button
            onClick={() => { onClose(); navigate('/saved') }}
            className="w-full text-xs text-blue-600 hover:text-blue-800 text-center hover:underline"
          >
            View all saved reports →
          </button>
        </div>
      </aside>
    </>
  )
}
