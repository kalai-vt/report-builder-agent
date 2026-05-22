import { useMemo, useState, useRef, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
} from '@tanstack/react-table'
import useStore from '../store/useStore'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatCell(value) {
  if (value === null || value === undefined)
    return <span className="text-gray-300 italic text-xs">—</span>
  if (typeof value === 'boolean') return value ? '✓' : '✗'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function isNumericColumn(data, key) {
  const samples = data.slice(0, 20).map((r) => r[key])
  const nonNull = samples.filter((v) => v !== null && v !== undefined && v !== '')
  if (nonNull.length === 0) return false
  return nonNull.every((v) => !isNaN(Number(v)))
}

function hasActiveFilter(f) {
  if (!f) return false
  const v = f.value
  if (Array.isArray(v)) return v.length > 0
  if (typeof v === 'object' && v !== null)
    return Object.values(v).some((x) => x !== '' && x !== null && x !== undefined)
  return v !== '' && v !== null && v !== undefined
}

// ─── Column Filter Dropdown (portal-rendered) ─────────────────────────────────

function ColumnFilterDropdown({ colKey, filterType, options, isNumeric, currentFilter, onFilterChange, onSort, onClose, anchorRect }) {
  const ref = useRef(null)
  const [localVal, setLocalVal] = useState(() => currentFilter?.value ?? (filterType === 'numeric_range' ? { min: '', max: '' } : filterType === 'categorical' ? [] : ''))
  const debounceRef = useRef(null)

  // Position below the anchor, flip up if needed
  const style = useMemo(() => {
    if (!anchorRect) return { display: 'none' }
    const panelW = 240
    const panelH = 280
    let left = anchorRect.left
    let top = anchorRect.bottom + 4

    if (left + panelW > window.innerWidth - 8) left = window.innerWidth - panelW - 8
    if (left < 8) left = 8
    if (top + panelH > window.innerHeight - 8) top = anchorRect.top - panelH - 4

    return { position: 'fixed', top, left, width: panelW, zIndex: 9999 }
  }, [anchorRect])

  // Close on outside click or Escape
  useEffect(() => {
    const handleClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) onClose()
    }
    const handleKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('mousedown', handleClick)
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('mousedown', handleClick)
      document.removeEventListener('keydown', handleKey)
    }
  }, [onClose])

  const commitText = (val) => {
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      onFilterChange({ type: 'text_search', value: val })
    }, 200)
  }

  const commitNumeric = (val) => {
    onFilterChange({ type: 'numeric_range', value: val })
  }

  const commitCategorical = (val) => {
    onFilterChange({ type: 'categorical', value: val })
  }

  const handleClear = () => {
    const empty = filterType === 'numeric_range' ? { min: '', max: '' } : filterType === 'categorical' ? [] : ''
    setLocalVal(empty)
    onFilterChange(null)
  }

  const isActive = hasActiveFilter(currentFilter)

  return createPortal(
    <div
      ref={ref}
      style={style}
      className="bg-white rounded-xl shadow-2xl border border-gray-200 overflow-hidden text-sm"
      onClick={(e) => e.stopPropagation()}
    >
      {/* Sort section */}
      <div className="px-3 pt-3 pb-2">
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1.5">Sort</p>
        <div className="flex gap-2">
          <button
            onClick={() => { onSort('asc'); onClose() }}
            className="flex-1 flex items-center gap-1.5 px-2 py-1.5 text-xs text-gray-700 border border-gray-200 rounded-lg hover:bg-blue-50 hover:border-blue-300 hover:text-blue-700 transition-colors"
          >
            <span className="text-base leading-none">↑</span>
            {isNumeric ? 'Low → High' : 'A → Z'}
          </button>
          <button
            onClick={() => { onSort('desc'); onClose() }}
            className="flex-1 flex items-center gap-1.5 px-2 py-1.5 text-xs text-gray-700 border border-gray-200 rounded-lg hover:bg-blue-50 hover:border-blue-300 hover:text-blue-700 transition-colors"
          >
            <span className="text-base leading-none">↓</span>
            {isNumeric ? 'High → Low' : 'Z → A'}
          </button>
        </div>
      </div>

      <div className="border-t border-gray-100" />

      {/* Filter section */}
      <div className="px-3 py-2.5">
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1.5">Filter</p>

        {/* Text filter */}
        {filterType === 'text_search' && (
          <input
            autoFocus
            type="text"
            value={typeof localVal === 'string' ? localVal : ''}
            onChange={(e) => {
              setLocalVal(e.target.value)
              commitText(e.target.value)
            }}
            placeholder="Search…"
            className="w-full text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-100 placeholder-gray-300"
          />
        )}

        {/* Numeric range filter */}
        {filterType === 'numeric_range' && (
          <div className="flex items-center gap-2">
            <input
              autoFocus
              type="number"
              value={typeof localVal === 'object' ? (localVal.min ?? '') : ''}
              onChange={(e) => {
                const next = { ...(typeof localVal === 'object' ? localVal : { min: '', max: '' }), min: e.target.value }
                setLocalVal(next)
                commitNumeric(next)
              }}
              placeholder="Min"
              className="flex-1 text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-100 min-w-0"
            />
            <span className="text-gray-400 text-xs flex-shrink-0">–</span>
            <input
              type="number"
              value={typeof localVal === 'object' ? (localVal.max ?? '') : ''}
              onChange={(e) => {
                const next = { ...(typeof localVal === 'object' ? localVal : { min: '', max: '' }), max: e.target.value }
                setLocalVal(next)
                commitNumeric(next)
              }}
              placeholder="Max"
              className="flex-1 text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-100 min-w-0"
            />
          </div>
        )}

        {/* Categorical filter */}
        {filterType === 'categorical' && (
          <div className="max-h-[160px] overflow-y-auto border border-gray-200 rounded-lg scrollbar-thin">
            <label className="flex items-center gap-2 px-3 py-1.5 hover:bg-gray-50 cursor-pointer border-b border-gray-100">
              <input
                type="checkbox"
                checked={Array.isArray(localVal) && localVal.length === 0}
                onChange={() => {
                  setLocalVal([])
                  commitCategorical([])
                }}
                className="w-3 h-3 accent-blue-600"
              />
              <span className="text-xs font-medium text-blue-600">All</span>
            </label>
            {options.map((opt) => {
              const checked = Array.isArray(localVal) && localVal.includes(String(opt))
              return (
                <label key={opt} className="flex items-center gap-2 px-3 py-1.5 hover:bg-gray-50 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => {
                      const next = checked
                        ? (Array.isArray(localVal) ? localVal : []).filter((s) => s !== String(opt))
                        : [...(Array.isArray(localVal) ? localVal : []), String(opt)]
                      setLocalVal(next)
                      commitCategorical(next)
                    }}
                    className="w-3 h-3 accent-blue-600"
                  />
                  <span className="text-xs text-gray-700 truncate">{String(opt)}</span>
                </label>
              )
            })}
          </div>
        )}
      </div>

      {/* Footer */}
      {isActive && (
        <div className="px-3 pb-3">
          <button
            onClick={handleClear}
            className="w-full text-xs text-red-500 border border-red-200 rounded-lg py-1.5 hover:bg-red-50 hover:text-red-700 transition-colors"
          >
            Clear filter
          </button>
        </div>
      )}
    </div>,
    document.body
  )
}

// ─── Main table ───────────────────────────────────────────────────────────────

export default function ReportTable() {
  const {
    filteredData,
    reportData,
    columns: colKeys,
    filterableColumns,
    hasData,
    page,
    totalPages,
    totalRows,
    pageSize,
    hasNextPage,
    hasPrevPage,
    loadPage,
    isLoading,
    explanation,
    applyFilters,
    clearFilters,
    activeFilters,
  } = useStore()

  const [sorting, setSorting] = useState([])
  const [localFilters, setLocalFilters] = useState({})
  const [openDropdown, setOpenDropdown] = useState(null) // colKey | null
  const [dropdownAnchor, setDropdownAnchor] = useState(null) // DOMRect

  // Reset filters when the query result changes (new columns)
  const prevColSig = useRef('')
  const colSig = colKeys.join(',')
  useEffect(() => {
    if (colSig !== prevColSig.current) {
      prevColSig.current = colSig
      setLocalFilters({})
      setOpenDropdown(null)
    }
  }, [colSig])

  const numericCols = useMemo(
    () => new Set(colKeys.filter((k) => isNumericColumn(filteredData.length ? filteredData : reportData, k))),
    [filteredData, reportData, colKeys]
  )

  const filterableMeta = useMemo(() => {
    const map = {}
    filterableColumns.forEach((fc) => { map[fc.column] = fc })
    return map
  }, [filterableColumns])

  const handleFilterChange = useCallback((col, filterVal) => {
    const next = filterVal === null
      ? (() => { const c = { ...localFilters }; delete c[col]; return c })()
      : { ...localFilters, [col]: filterVal }
    setLocalFilters(next)
    applyFilters(next)
  }, [localFilters, applyFilters])

  const handleClearAll = useCallback(() => {
    setLocalFilters({})
    clearFilters()
    setOpenDropdown(null)
  }, [clearFilters])

  const activeCount = useMemo(
    () => Object.values(localFilters).filter(hasActiveFilter).length,
    [localFilters]
  )

  const toggleDropdown = (colKey, e) => {
    e.stopPropagation()
    if (openDropdown === colKey) {
      setOpenDropdown(null)
      return
    }
    const rect = e.currentTarget.getBoundingClientRect()
    setDropdownAnchor(rect)
    setOpenDropdown(colKey)
  }

  const columns = useMemo(
    () =>
      colKeys.map((key) => ({
        id: key,
        accessorKey: key,
        header: key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()),
        cell: (info) => {
          const val = info.getValue()
          return (
            <div
              className={`text-xs px-1 truncate max-w-[200px] ${numericCols.has(key) ? 'text-right font-mono' : ''}`}
              title={val !== null && val !== undefined ? String(val) : ''}
            >
              {formatCell(val)}
            </div>
          )
        },
        meta: { numeric: numericCols.has(key) },
      })),
    [colKeys, numericCols]
  )

  const table = useReactTable({
    data: filteredData,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    state: { sorting },
    onSortingChange: setSorting,
    manualPagination: true,
  })

  if (!hasData) return null

  return (
    <div className="flex flex-col gap-2">
      {/* Explanation */}
      {explanation && (
        <div className="text-xs text-gray-500 italic px-1 mb-1">{explanation}</div>
      )}

      {/* Active filter summary bar */}
      {activeCount > 0 && (
        <div className="flex items-center gap-3 px-1">
          <div className="flex items-center gap-1.5 text-xs text-blue-600">
            <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M3 3a1 1 0 011-1h12a1 1 0 011 1v3a1 1 0 01-.293.707L13 10.414V15a1 1 0 01-.553.894l-4 2A1 1 0 017 17v-6.586L3.293 6.707A1 1 0 013 6V3z" clipRule="evenodd" />
            </svg>
            <span>{activeCount} filter{activeCount > 1 ? 's' : ''} active</span>
            <span className="text-gray-400">·</span>
            <span>{filteredData.length.toLocaleString()} of {reportData.length.toLocaleString()} rows</span>
          </div>
          <button
            onClick={handleClearAll}
            className="text-xs text-red-500 hover:text-red-700 hover:underline"
          >
            Clear all
          </button>
        </div>
      )}

      {/* Table */}
      <div className="border border-gray-200 rounded-lg overflow-hidden">
        <div className="overflow-x-auto scrollbar-thin">
          <div className="max-h-[calc(100vh-360px)] overflow-y-auto scrollbar-thin">
            <table className="w-full text-sm border-collapse">

              {/* Sticky header */}
              <thead className="sticky top-0 z-10 bg-gray-50 border-b border-gray-200">
                {table.getHeaderGroups().map((headerGroup) => (
                  <tr key={headerGroup.id}>
                    {/* Row number column */}
                    <th className="w-8 px-2 py-2.5 text-xs text-gray-400 font-medium text-right border-r border-gray-100">
                      #
                    </th>

                    {headerGroup.headers.map((header) => {
                      const isSorted = header.column.getIsSorted()
                      const isNumeric = header.column.columnDef.meta?.numeric
                      const colKey = header.column.id
                      const isOpen = openDropdown === colKey
                      const isFiltered = hasActiveFilter(localFilters[colKey])

                      return (
                        <th
                          key={header.id}
                          className={`px-0 py-0 text-xs font-semibold whitespace-nowrap select-none border-r border-gray-100 last:border-r-0 group ${
                            isFiltered ? 'bg-blue-50' : ''
                          }`}
                        >
                          <div
                            className={`flex items-center justify-between gap-1 px-3 py-2.5 cursor-pointer hover:bg-gray-100 transition-colors ${isNumeric ? 'flex-row-reverse' : ''}`}
                            onClick={header.column.getToggleSortingHandler()}
                          >
                            {/* Label + sort arrow */}
                            <div className={`flex items-center gap-1 min-w-0 ${isNumeric ? 'flex-row-reverse' : ''}`}>
                              <span className={`truncate ${isFiltered ? 'text-blue-700' : 'text-gray-600'}`}>
                                {flexRender(header.column.columnDef.header, header.getContext())}
                              </span>
                              {isSorted === 'asc' && <span className="text-blue-500 flex-shrink-0">↑</span>}
                              {isSorted === 'desc' && <span className="text-blue-500 flex-shrink-0">↓</span>}
                            </div>

                            {/* Filter trigger button */}
                            <button
                              onClick={(e) => toggleDropdown(colKey, e)}
                              title="Filter / Sort"
                              className={`flex-shrink-0 flex items-center justify-center w-5 h-5 rounded transition-colors ${
                                isFiltered
                                  ? 'text-blue-600 bg-blue-100 hover:bg-blue-200'
                                  : isOpen
                                  ? 'text-gray-700 bg-gray-200'
                                  : 'text-gray-300 hover:text-gray-600 hover:bg-gray-200 opacity-0 group-hover:opacity-100'
                              }`}
                            >
                              {isFiltered ? (
                                /* Filled funnel when filter is active */
                                <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                                  <path fillRule="evenodd" d="M3 3a1 1 0 011-1h12a1 1 0 011 1v3a1 1 0 01-.293.707L13 10.414V15a1 1 0 01-.553.894l-4 2A1 1 0 017 17v-6.586L3.293 6.707A1 1 0 013 6V3z" clipRule="evenodd" />
                                </svg>
                              ) : (
                                /* Outline chevron otherwise */
                                <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                                </svg>
                              )}
                            </button>
                          </div>

                          {/* Active filter chip below label */}
                          {isFiltered && (
                            <div className="px-3 pb-1.5 flex items-center gap-1">
                              <span className="text-xs text-blue-600 bg-blue-100 px-1.5 py-0.5 rounded-full truncate max-w-[120px]">
                                {(() => {
                                  const f = localFilters[colKey]
                                  if (!f) return ''
                                  if (f.type === 'text_search') return f.value
                                  if (f.type === 'numeric_range') {
                                    const { min, max } = f.value || {}
                                    if (min && max) return `${min} – ${max}`
                                    if (min) return `≥ ${min}`
                                    if (max) return `≤ ${max}`
                                  }
                                  if (f.type === 'categorical') return `${f.value.length} selected`
                                  return ''
                                })()}
                              </span>
                              <button
                                onClick={(e) => { e.stopPropagation(); handleFilterChange(colKey, null) }}
                                className="text-blue-400 hover:text-red-500 flex-shrink-0"
                              >
                                <svg className="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" />
                                </svg>
                              </button>
                            </div>
                          )}
                        </th>
                      )
                    })}
                  </tr>
                ))}
              </thead>

              {/* Body */}
              <tbody className="bg-white divide-y divide-gray-100">
                {table.getRowModel().rows.length === 0 ? (
                  <tr>
                    <td colSpan={colKeys.length + 1} className="py-12 text-center text-sm text-gray-400">
                      No rows match the current filters.
                    </td>
                  </tr>
                ) : (
                  table.getRowModel().rows.map((row, idx) => (
                    <tr key={row.id} className="hover:bg-blue-50/30 transition-colors">
                      <td className="px-2 py-1.5 text-xs text-gray-300 text-right border-r border-gray-100 w-8">
                        {(page - 1) * pageSize + idx + 1}
                      </td>
                      {row.getVisibleCells().map((cell) => (
                        <td key={cell.id} className="px-3 py-1.5 border-r border-gray-50 last:border-r-0">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between px-1">
          <p className="text-xs text-gray-500">
            Page {page} of {totalPages} · {totalRows.toLocaleString()} total rows
          </p>
          <div className="flex items-center gap-1">
            <button onClick={() => loadPage(1)} disabled={!hasPrevPage || isLoading}
              className="px-2 py-1 text-xs border border-gray-200 rounded hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed">«</button>
            <button onClick={() => loadPage(page - 1)} disabled={!hasPrevPage || isLoading}
              className="px-2.5 py-1 text-xs border border-gray-200 rounded hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed">‹ Prev</button>

            {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
              let p
              if (totalPages <= 5) p = i + 1
              else if (page <= 3) p = i + 1
              else if (page >= totalPages - 2) p = totalPages - 4 + i
              else p = page - 2 + i
              return (
                <button key={p} onClick={() => loadPage(p)} disabled={isLoading}
                  className={`w-7 py-1 text-xs rounded border transition-colors ${
                    p === page ? 'bg-blue-600 text-white border-blue-600' : 'border-gray-200 hover:bg-gray-50 text-gray-600'
                  }`}>{p}</button>
              )
            })}

            <button onClick={() => loadPage(page + 1)} disabled={!hasNextPage || isLoading}
              className="px-2.5 py-1 text-xs border border-gray-200 rounded hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed">Next ›</button>
            <button onClick={() => loadPage(totalPages)} disabled={!hasNextPage || isLoading}
              className="px-2 py-1 text-xs border border-gray-200 rounded hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed">»</button>
          </div>
        </div>
      )}

      {/* Portal-rendered filter dropdown */}
      {openDropdown && (() => {
        const colKey = openDropdown
        const meta = filterableMeta[colKey]
        const isNumeric = numericCols.has(colKey)
        let filterType = meta?.filter_type
        if (!filterType) filterType = isNumeric ? 'numeric_range' : 'text_search'

        return (
          <ColumnFilterDropdown
            key={colKey}
            colKey={colKey}
            filterType={filterType}
            options={meta?.values || []}
            isNumeric={isNumeric}
            currentFilter={localFilters[colKey]}
            onFilterChange={(f) => handleFilterChange(colKey, f)}
            onSort={(dir) => {
              setSorting([{ id: colKey, desc: dir === 'desc' }])
            }}
            onClose={() => setOpenDropdown(null)}
            anchorRect={dropdownAnchor}
          />
        )
      })()}
    </div>
  )
}
