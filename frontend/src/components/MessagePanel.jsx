import useStore from '../store/useStore'

const STATUS_META = {
  greeting: {
    icon: '👋',
    title: 'Hello!',
    bg: 'bg-blue-50 border-blue-200',
    title_color: 'text-blue-800',
    text_color: 'text-blue-700',
  },
  off_topic: {
    icon: '🤔',
    title: 'Off Topic',
    bg: 'bg-gray-50 border-gray-200',
    title_color: 'text-gray-700',
    text_color: 'text-gray-600',
  },
  filter_redirect: {
    icon: '🔍',
    title: 'Use Filters',
    bg: 'bg-blue-50 border-blue-200',
    title_color: 'text-blue-800',
    text_color: 'text-blue-700',
  },
  restricted_operation: {
    icon: '🚫',
    title: 'Operation Restricted',
    bg: 'bg-red-50 border-red-200',
    title_color: 'text-red-800',
    text_color: 'text-red-700',
  },
}

export default function MessagePanel() {
  const { status, message, suggestions, clearMessage } = useStore()
  if (!['greeting', 'off_topic', 'filter_redirect', 'restricted_operation'].includes(status)) return null

  const meta = STATUS_META[status] || STATUS_META.off_topic

  return (
    <div className={`rounded-lg border p-4 mb-4 ${meta.bg}`}>
      <div className="flex items-start gap-3">
        <span className="text-2xl leading-none">{meta.icon}</span>
        <div className="flex-1">
          <p className={`font-semibold text-sm ${meta.title_color}`}>{meta.title}</p>
          {message && <p className={`text-sm mt-1 ${meta.text_color}`}>{message}</p>}
          {suggestions && suggestions.length > 0 && (
            <div className="mt-3">
              <p className={`text-xs font-medium mb-2 ${meta.title_color}`}>Try asking:</p>
              <div className="flex flex-wrap gap-2">
                {suggestions.map((s, i) => (
                  <button
                    key={i}
                    onClick={() => {
                      clearMessage()
                      useStore.getState().generateReport(s)
                    }}
                    className="text-xs px-3 py-1.5 bg-white border border-blue-300 text-blue-700 rounded-full hover:bg-blue-50 transition-colors"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
        <button onClick={clearMessage} className="text-gray-400 hover:text-gray-600 text-lg leading-none">×</button>
      </div>
    </div>
  )
}
