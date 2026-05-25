import axios from 'axios'

const http = axios.create({
  baseURL: '/api/v1',
  headers: { 'Content-Type': 'application/json' },
  timeout: 300000,
})

export const generateReport = (payload) => http.post('/report/generate', payload)
export const clarifyReport = (payload) => http.post('/report/clarify', payload)

export const refreshReport = (sessionId, userId, userRole = 'employee', page = 1, pageSize = 0) =>
  http.get(`/report/refresh/${sessionId}`, {
    params: { user_id: userId, user_role: userRole, page, page_size: pageSize },
  })

export const getReportPage = (sessionId, userId, page, pageSize = 0) =>
  http.get(`/report/page/${sessionId}`, {
    params: { user_id: userId, page, page_size: pageSize },
  })

export const createWebSocket = (sessionId, userId, userRole = 'employee', interval = 30) => {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  const params = new URLSearchParams({ user_id: userId, user_role: userRole, interval })
  return new WebSocket(`${protocol}//${host}/api/v1/report/stream/${sessionId}?${params}`)
}
