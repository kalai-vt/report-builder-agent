import * as XLSX from 'xlsx'
import { jsPDF } from 'jspdf'
import autoTable from 'jspdf-autotable'

function sanitizeRows(data, columns) {
  return data.map((row) => {
    const obj = {}
    columns.forEach((col) => {
      obj[col] = row[col] ?? ''
    })
    return obj
  })
}

export function exportToCSV(data, columns, filename = 'report') {
  if (!data.length) return
  const escape = (v) => {
    const s = String(v ?? '')
    return s.includes(',') || s.includes('"') || s.includes('\n') ? `"${s.replace(/"/g, '""')}"` : s
  }
  const lines = [columns.map(escape).join(',')]
  data.forEach((row) => lines.push(columns.map((col) => escape(row[col] ?? '')).join(',')))
  const blob = new Blob([lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' })
  triggerDownload(blob, `${filename}.csv`)
}

export function exportToExcel(data, columns, filename = 'report') {
  if (!data.length) return
  const ws = XLSX.utils.json_to_sheet(sanitizeRows(data, columns))
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, 'Report')
  XLSX.writeFile(wb, `${filename}.xlsx`)
}

export function exportToPDF(data, columns, filename = 'report', title = 'AI Report Builder') {
  if (!data.length) return
  const doc = new jsPDF({ orientation: columns.length > 6 ? 'landscape' : 'portrait' })
  doc.setFontSize(14)
  doc.text(title, 14, 15)
  doc.setFontSize(9)
  doc.setTextColor(100)
  doc.text(`Exported: ${new Date().toLocaleString()}  |  Rows: ${data.length}`, 14, 22)
  autoTable(doc, {
    startY: 28,
    head: [columns],
    body: data.map((row) => columns.map((col) => String(row[col] ?? ''))),
    styles: { fontSize: 7, cellPadding: 2, overflow: 'linebreak' },
    headStyles: { fillColor: [37, 99, 235], textColor: 255, fontStyle: 'bold' },
    alternateRowStyles: { fillColor: [245, 247, 250] },
    margin: { left: 14, right: 14 },
  })
  doc.save(`${filename}.pdf`)
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
