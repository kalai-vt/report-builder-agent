import { Link, useLocation } from 'react-router-dom'
import useStore from '../store/useStore'

export default function Header({ onToggleSidebar }) {
  const location = useLocation()
  const { userId, userRole, setUserId, setUserRole } = useStore()

  return (
    <header className="flex items-center gap-4 px-4 py-3 bg-white border-b border-gray-200 shadow-sm flex-shrink-0">
      {/* Sidebar toggle */}
      <button
        onClick={onToggleSidebar}
        className="p-2 rounded hover:bg-gray-100 text-gray-500 hover:text-gray-700 transition-colors"
        title="Saved Reports"
      >
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>

      {/* Logo + Title */}
      <div className="flex items-center gap-2">
        <div className="w-7 h-7 rounded-lg bg-blue-600 flex items-center justify-center">
          <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
        </div>
        <span className="font-semibold text-gray-800 text-base">AI Report Builder</span>
      </div>

      {/* Nav */}
      <nav className="flex gap-1 ml-2">
        <Link
          to="/"
          className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
            location.pathname === '/'
              ? 'bg-blue-50 text-blue-700'
              : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
          }`}
        >
          Builder
        </Link>
        <Link
          to="/saved"
          className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
            location.pathname === '/saved'
              ? 'bg-blue-50 text-blue-700'
              : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
          }`}
        >
          Saved Reports
        </Link>
      </nav>

      {/* Spacer */}
      <div className="flex-1" />

      {/* User controls */}
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
          className="text-xs border border-gray-200 rounded px-2 py-1 w-28 text-gray-600 focus:outline-none focus:border-blue-400"
          placeholder="User ID"
          title="User ID"
        />
        <select
          value={userRole}
          onChange={(e) => setUserRole(e.target.value)}
          className="text-xs border border-gray-200 rounded px-2 py-1 text-gray-600 focus:outline-none focus:border-blue-400 bg-white"
          title="Role"
        >
          <option value="employee">Employee</option>
          <option value="lead">Lead</option>
          <option value="manager">Manager</option>
          <option value="hr">HR</option>
        </select>
      </div>
    </header>
  )
}
