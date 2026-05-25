import { useState } from 'react'
import useStore from '../store/useStore'
import { exportToCSV, exportToExcel, exportToPDF } from '../utils/exportUtils'

export default function ExportButtons() {
  const { filteredData, columns, currentPrompt } = useStore()
  const [saving, setSaving] = useState(false)
  const [saveName, setSaveName] = useState('')
  const [showSaveInput, setShowSaveInput] = useState(false)
  const saveReport = useStore((s) => s.saveReport)

  if (!filteredData.length) return null

  const filename = `report_${Date.now()}`
  const title = currentPrompt ? currentPrompt.slice(0, 60) : 'AI Report Builder'

  const handleSave = (e) => {
    e.preventDefault()
    saveReport(saveName || currentPrompt)
    setSaveName('')
    setShowSaveInput(false)
  }

  return (
    <div className="flex items-center gap-2 flex-wrap">
      {/* Export buttons */}
      <span className="text-xs text-gray-400 mr-1">Export:</span>

      <button
        onClick={() => exportToCSV(filteredData, columns, filename)}
        className="inline-flex items-center gap-1 text-xs px-2.5 py-1.5 rounded border border-gray-200 text-gray-600 hover:bg-gray-50 hover:border-gray-300 transition-colors"
      >
        <svg className="w-3.5 h-3.5 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
        </svg>
        CSV
      </button>

      <button
        onClick={() => exportToExcel(filteredData, columns, filename)}
        className="inline-flex items-center gap-1 text-xs px-2.5 py-1.5 rounded border border-gray-200 text-gray-600 hover:bg-gray-50 hover:border-gray-300 transition-colors"
      >
        <svg className="w-3.5 h-3.5 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
        Excel
      </button>

      <button
        onClick={() => exportToPDF(filteredData, columns, filename, title)}
        className="inline-flex items-center gap-1 text-xs px-2.5 py-1.5 rounded border border-gray-200 text-gray-600 hover:bg-gray-50 hover:border-gray-300 transition-colors"
      >
        <svg className="w-3.5 h-3.5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
        </svg>
        PDF
      </button>

      <div className="w-px h-4 bg-gray-200 mx-1" />

      {/* Save report */}
      {showSaveInput ? (
        <form onSubmit={handleSave} className="flex items-center gap-1">
          <input
            autoFocus
            type="text"
            value={saveName}
            onChange={(e) => setSaveName(e.target.value)}
            placeholder="Report name…"
            className="text-xs border border-blue-300 rounded px-2 py-1.5 w-36 focus:outline-none focus:border-blue-500"
          />
          <button
            type="submit"
            className="text-xs px-2.5 py-1.5 bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors"
          >
            Save
          </button>
          <button
            type="button"
            onClick={() => setShowSaveInput(false)}
            className="text-xs px-2 py-1.5 text-gray-400 hover:text-gray-600"
          >
            ×
          </button>
        </form>
      ) : (
        <button
          onClick={() => setShowSaveInput(true)}
          className="inline-flex items-center gap-1 text-xs px-2.5 py-1.5 rounded border border-gray-200 text-gray-600 hover:bg-blue-50 hover:border-blue-300 hover:text-blue-600 transition-colors"
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
          </svg>
          Save Report
        </button>
      )}
    </div>
  )
}
