import { useState, useRef } from 'react'
import useStore from '../store/useStore'

const EXAMPLE_QUERIES = [
  'Show all active KRAs with employee names and current progress',
  'Show employee productivity for last month',
  'List KRAs pending approval by manager',
  'Show top 10 employees by KRA completion rate',
]

export default function QueryInput() {
  const [query, setQuery] = useState('')
  const { generateReport, isLoading, hasData, clearFilters, reset, clarification } = useStore()
  const textareaRef = useRef(null)

  const handleSubmit = async (e) => {
    e.preventDefault()
    const trimmed = query.trim()
    if (!trimmed || isLoading) return
    clearFilters()
    await generateReport(trimmed)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit(e)
    }
  }

  const useExample = (q) => {
    setQuery(q)
    textareaRef.current?.focus()
  }

  const handleReset = () => {
    setQuery('')
    reset()
  }

  return (
    <div className="border-t border-gray-200 bg-white px-4 py-3 flex-shrink-0">
      {/* Example chips (shown when no data) */}
      {!hasData && !isLoading && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {EXAMPLE_QUERIES.map((q) => (
            <button
              key={q}
              onClick={() => useExample(q)}
              className="text-xs px-2.5 py-1 rounded-full border border-gray-200 text-gray-500 hover:border-blue-400 hover:text-blue-600 hover:bg-blue-50 transition-colors"
            >
              {q.length > 50 ? q.slice(0, 50) + '…' : q}
            </button>
          ))}
        </div>
      )}

      <form onSubmit={handleSubmit} className="flex items-end gap-2">
        {/* Textarea */}
        <div className="flex-1 relative">
          <textarea
            ref={textareaRef}
            rows={2}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isLoading}
            placeholder="Ask a question about your KRA data… (Enter to send, Shift+Enter for new line)"
            className="w-full resize-none rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 disabled:bg-gray-50 disabled:text-gray-400 scrollbar-thin"
          />
        </div>

        {/* Buttons */}
        <div className="flex flex-col gap-1.5">
          <button
            type="submit"
            disabled={!query.trim() || isLoading}
            className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {isLoading ? (
              <>
                <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                Running…
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
                Run
              </>
            )}
          </button>

          {(hasData || query) && (
            <button
              type="button"
              onClick={handleReset}
              className="px-4 py-1.5 text-xs text-gray-500 border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
            >
              Clear
            </button>
          )}
        </div>
      </form>

      {clarification && !isLoading && (
        <p className="mt-1.5 text-xs text-amber-500 text-center">
          ↑ Please answer the clarification question above, or type a completely new query here to start over.
        </p>
      )}

      <p className="mt-1.5 text-xs text-gray-400 text-right">
        KRA data · GPT-4o-mini · MySQL
      </p>
    </div>
  )
}
