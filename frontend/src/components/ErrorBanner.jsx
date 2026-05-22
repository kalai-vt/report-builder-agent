import useStore from '../store/useStore'

const ERROR_META = {
  SESSION_EXPIRED: {
    icon: '⏰',
    label: 'Session Expired',
    color: 'bg-amber-50 border-amber-300 text-amber-800',
    iconColor: 'text-amber-500',
  },
  ACCESS_DENIED: {
    icon: '🔒',
    label: 'Access Denied',
    color: 'bg-red-50 border-red-300 text-red-800',
    iconColor: 'text-red-500',
  },
  SCHEMA_CHANGED: {
    icon: '🔄',
    label: 'Schema Changed',
    color: 'bg-orange-50 border-orange-300 text-orange-800',
    iconColor: 'text-orange-500',
  },
}

export default function ErrorBanner() {
  const { error, errorCode, clearError } = useStore()
  if (!error) return null

  const meta = ERROR_META[errorCode] || {
    icon: '⚠️',
    label: 'Error',
    color: 'bg-red-50 border-red-300 text-red-800',
    iconColor: 'text-red-500',
  }

  return (
    <div className={`flex items-start gap-3 px-4 py-3 rounded-lg border mb-3 ${meta.color}`}>
      <span className="text-lg leading-none mt-0.5">{meta.icon}</span>
      <div className="flex-1 min-w-0">
        <p className="font-semibold text-sm">{meta.label}</p>
        <p className="text-sm mt-0.5 opacity-90">{error}</p>
      </div>
      <button
        onClick={clearError}
        className="opacity-60 hover:opacity-100 transition-opacity text-lg leading-none"
        title="Dismiss"
      >
        ×
      </button>
    </div>
  )
}
