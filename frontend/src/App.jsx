import { BrowserRouter, Routes, Route } from 'react-router-dom'
import ReportBuilderPage from './pages/ReportBuilderPage'
import SavedReportsPage from './pages/SavedReportsPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<ReportBuilderPage />} />
        <Route path="/saved" element={<SavedReportsPage />} />
      </Routes>
    </BrowserRouter>
  )
}
