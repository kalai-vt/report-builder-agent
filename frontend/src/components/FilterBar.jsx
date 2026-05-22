import { useState, useEffect, useRef } from 'react'
import useStore from '../store/useStore'

// ─── Individual filter controls ──────────────────────────────────────────────

function TextFilter({ column, label, value, onChange }) {
  return (
    <div className="flex flex-col gap-1 min-w-[140px]">
      <label className="text-xs font-medium text-gray-500 truncate">{label}</label>
      <input
        type="text"
        value={value || ''}
        onChange={(e) => onChange({ type: 'text_search', value: e.target.value })}
        placeholder={`Filter ${label}…`}
        className="text-xs border border-gray-200 rounded px-2 py-1.5 focus:outline-none focus:border-blue-400 placeholder-gray-300 bg-white"
      />
    </div>
  )
}

function NumericRangeFilter({ column, label, value, onChange }) {
  const val = value?.value || { min: '', max: '' }
  return (
    <div className="flex flex-col gap-1 min-w-[160px]">
      <label className="text-xs font-medium text-gray-500 truncate">{label}</label>
      <div className="flex gap-1">
        <input
          type="number"
          value={val.min}
          onChange={(e) => onChange({ type: 'numeric_range', value: { ...val, min: e.target.value } })}
          placeholder="Min"
          className="text-xs border border-gray-200 rounded px-2 py-1.5 w-16 focus:outline-none focus:border-blue-400 bg-white"
        />
        <span className="text-gray-400 self-center text-xs">–</span>
        <input
          type="number"
          value={val.max}
          onChange={(e) => onChange({ type: 'numeric_range', value: { ...val, max: e.target.value } })}
          placeholder="Max"
          className="text-xs border border-gray-200 rounded px-2 py-1.5 w-16 focus:outline-none focus:border-blue-400 bg-white"
        />
      </div>
    </div>
  )
}

function DateRangeFilter({ column, label, value, onChange }) {
  const val = value?.value || { from: '', to: '' }
  return (
    <div className="flex flex-col gap-1 min-w-[200px]">
      <label className="text-xs font-medium text-gray-500 truncate">{label}</label>
      <div className="flex gap-1 items-center">
        <input
          type="date"
          value={val.from}
          onChange={(e) => onChange({ type: 'date_range', value: { ...val, from: e.target.value } })}
          className="text-xs border border-gray-200 rounded px-2 py-1.5 focus:outline-none focus:border-blue-400 bg-white"
        />
        <span className="text-gray-400 text-xs">to</span>
        <input
          type="date"
          value={val.to}
          onChange={(e) => onChange({ type: 'date_range', value: { ...val, to: e.target.value } })}
          className="text-xs border border-gray-200 rounded px-2 py-1.5 focus:outline-none focus:border-blue-400 bg-white"
        />
      </div>
    </div>
  )
}

function CategoricalFilter({ column, label, options, value, onChange }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  const selected = value?.value || []

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const toggle = (opt) => {
    const next = selected.includes(opt) ? selected.filter((s) => s !== opt) : [...selected, opt]
    onChange({ type: 'categorical', value: next })
  }

  const selectAll = () => onChange({ type: 'categorical', value: [] })

  return (
    <div className="flex flex-col gap-1 min-w-[140px]" ref={ref}>
      <label className="text-xs font-medium text-gray-500 truncate">{label}</label>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="text-xs border border-gray-200 rounded px-2 py-1.5 text-left bg-white hover:border-blue-400 flex items-center justify-between gap-1 focus:outline-none focus:border-blue-400"
      >
        <span className="truncate text-gray-600">
          {selected.length === 0 ? `All ${label}` : `${selected.length} selected`}
        </span>
        <svg className={`w-3 h-3 text-gray-400 flex-shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}
          fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="absolute z-20 mt-[58px] bg-white border border-gray-200 rounded-lg shadow-lg min-w-[160px] max-h-52 overflow-y-auto py-1 scrollbar-thin">
          <button
            onClick={selectAll}
            className="w-full text-left px-3 py-1.5 text-xs text-blue-600 hover:bg-blue-50 font-medium"
          >
            {selected.length === 0 ? '✓ All' : 'Clear selection'}
          </button>
          <div className="border-t border-gray-100 my-1" />
          {options.map((opt) => (
            <label key={opt} className="flex items-center gap-2 px-3 py-1.5 hover:bg-gray-50 cursor-pointer">
              <input
                type="checkbox"
                checked={selected.includes(opt)}
                onChange={() => toggle(opt)}
                className="w-3 h-3 accent-blue-600"
              />
              <span className="text-xs text-gray-700 truncate">{opt}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── FilterBar ────────────────────────────────────────────────────────────────

export default function FilterBar() {
  const { filterableColumns, columns, applyFilters, clearFilters, activeFilters, hasData } = useStore()
  const [localFilters, setLocalFilters] = useState({})
  const debounceRef = useRef({})

  if (!hasData) return null

  // Use backend-provided filterable_columns; fall back to text search for all columns
  const effectiveCols =
    filterableColumns.length > 0
      ? filterableColumns
      : columns.map((col) => ({ column: col, label: formatLabel(col), filter_type: 'text_search', values: [] }))

  const activeCount = Object.values(localFilters).filter((f) => {
    if (!f) return false
    const v = f.value
    if (Array.isArray(v)) return v.length > 0
    if (typeof v === 'object' && v !== null) return Object.values(v).some((x) => x !== '' && x !== null)
    return v !== '' && v !== null && v !== undefined
  }).length

  const handleChange = (col, filterVal) => {
    const next = { ...localFilters, [col]: filterVal }
    setLocalFilters(next)

    // Debounce text input, instant for others
    if (filterVal?.type === 'text_search') {
      clearTimeout(debounceRef.current[col])
      debounceRef.current[col] = setTimeout(() => applyFilters(next), 250)
    } else {
      applyFilters(next)
    }
  }

  const handleClear = () => {
    setLocalFilters({})
    clearFilters()
  }

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-3 mb-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <svg className="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2a1 1 0 01-.293.707l-6.414 6.414A1 1 0 0014 13.828v5.172a1 1 0 01-.553.894l-4 2A1 1 0 018 21v-7.172a1 1 0 00-.293-.707L1.293 6.707A1 1 0 011 6V4z" />
          </svg>
          <span className="text-xs font-medium text-gray-600">
            Filters
            {activeCount > 0 && (
              <span className="ml-1.5 px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded-full text-xs">
                {activeCount} active
              </span>
            )}
          </span>
          <span className="text-xs text-gray-400">(client-side only — no API calls)</span>
        </div>
        {activeCount > 0 && (
          <button
            onClick={handleClear}
            className="text-xs text-red-500 hover:text-red-700 hover:underline transition-colors"
          >
            Clear all
          </button>
        )}
      </div>

      <div className="flex flex-wrap gap-3 relative">
        {effectiveCols.map((fc) => {
          const currentFilter = localFilters[fc.column]
          const props = {
            key: fc.column,
            column: fc.column,
            label: fc.label || formatLabel(fc.column),
            value: currentFilter,
            onChange: (f) => handleChange(fc.column, f),
          }

          if (fc.filter_type === 'categorical') {
            return <CategoricalFilter {...props} options={fc.values || []} />
          }
          if (fc.filter_type === 'numeric_range') {
            return <NumericRangeFilter {...props} />
          }
          if (fc.filter_type === 'date_range') {
            return <DateRangeFilter {...props} />
          }
          return <TextFilter {...props} />
        })}
      </div>
    </div>
  )
}

function formatLabel(col) {
  return col.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}
