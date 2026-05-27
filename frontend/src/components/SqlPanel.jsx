import { useState } from 'react'
import useStore from '../store/useStore'

export default function SqlPanel() {
  const { sqlQuery } = useStore()
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)

  if (!sqlQuery) return null

  const copy = () => {
    navigator.clipboard.writeText(sqlQuery)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="mb-3 border border-gray-200 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-3 py-2 bg-gray-50 hover:bg-gray-100 transition-colors text-xs text-gray-500"
      >
        <div className="flex items-center gap-1.5">
          <svg className="w-3.5 h-3.5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
          </svg>
          <span className="font-medium text-gray-600">Generated SQL</span>
        </div>
        <svg
          className={`w-3.5 h-3.5 text-gray-400 transition-transform ${open ? 'rotate-180' : ''}`}
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="relative bg-gray-900">
          <button
            onClick={copy}
            className="absolute top-2 right-2 text-xs px-2 py-1 rounded bg-gray-700 text-gray-300 hover:bg-gray-600 transition-colors"
          >
            {copied ? '✓ Copied' : 'Copy'}
          </button>
          <pre className="text-xs text-green-300 p-4 overflow-x-auto scrollbar-thin font-mono leading-relaxed whitespace-pre-wrap pr-16">
            {sqlQuery}
          </pre>
        </div>
      )}
    </div>
  )
}
