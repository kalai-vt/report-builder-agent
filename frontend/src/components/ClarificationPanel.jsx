import { useState } from 'react'
import useStore from '../store/useStore'

export default function ClarificationPanel() {
  const { clarification, clarifyReport, isLoading } = useStore()
  const [customAnswer, setCustomAnswer] = useState('')

  if (!clarification) return null

  const { follow_up_question, follow_up_options, clarification_round } = clarification

  const handleOption = (opt) => {
    setCustomAnswer('')
    clarifyReport(opt)
  }

  const handleCustom = (e) => {
    e.preventDefault()
    const trimmed = customAnswer.trim()
    if (!trimmed || isLoading) return
    setCustomAnswer('')
    clarifyReport(trimmed)
  }

  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 mb-4">
      {/* Header */}
      <div className="flex items-center gap-2 mb-3">
        <div className="w-7 h-7 rounded-full bg-blue-600 flex items-center justify-center flex-shrink-0">
          <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </div>
        <div>
          <p className="text-xs text-blue-600 font-medium">Clarification needed</p>
          {clarification_round > 0 && (
            <p className="text-xs text-blue-400">Round {clarification_round + 1} of 2</p>
          )}
        </div>
      </div>

      {/* Question */}
      <p className="text-sm font-semibold text-blue-800 mb-3">{follow_up_question}</p>

      {/* Option buttons */}
      {follow_up_options && follow_up_options.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-3">
          {follow_up_options.map((opt, i) => (
            <button
              key={i}
              type="button"
              onClick={() => handleOption(opt)}
              disabled={isLoading}
              className="px-3 py-1.5 text-sm bg-white border border-blue-300 text-blue-700 rounded-lg hover:bg-blue-100 hover:border-blue-400 disabled:opacity-50 transition-colors"
            >
              {opt}
            </button>
          ))}
        </div>
      )}

      {/* Custom answer */}
      <form onSubmit={handleCustom} className="flex gap-2">
        <input
          type="text"
          value={customAnswer}
          onChange={(e) => setCustomAnswer(e.target.value)}
          disabled={isLoading}
          placeholder="Or type your own answer…"
          className="flex-1 text-sm border border-blue-300 rounded-lg px-3 py-1.5 bg-white focus:outline-none focus:border-blue-500 placeholder-blue-300 text-blue-800"
        />
        <button
          type="submit"
          disabled={!customAnswer.trim() || isLoading}
          className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
        >
          {isLoading ? '…' : 'Send'}
        </button>
      </form>
    </div>
  )
}
