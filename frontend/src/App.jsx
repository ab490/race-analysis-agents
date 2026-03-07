import { useState } from 'react'
import { getStoredApiKey, setStoredApiKey } from './api'
import ChatPage from './pages/ChatPage'
import UploadPage from './pages/UploadPage'

export default function App() {
  const [page, setPage] = useState('chat') // 'chat' | 'upload'
  const [sessionId, setSessionId] = useState(null)
  const [apiKey, setApiKey] = useState(getStoredApiKey)

  function handleKeyChange(e) {
    const val = e.target.value
    setApiKey(val)
    setStoredApiKey(val)
  }

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-100 flex flex-col">
      {/* Navbar */}
      <header className="border-b border-slate-800 px-6 py-3 flex items-center gap-6">
        <span className="font-bold text-lg tracking-tight text-white">Race Analysis</span>
        <nav className="flex gap-4 text-sm">
          <button
            onClick={() => setPage('chat')}
            className={`px-3 py-1 rounded transition ${page === 'chat' ? 'bg-slate-700 text-white' : 'text-slate-400 hover:text-white'}`}
          >
            Chat
          </button>
          <button
            onClick={() => setPage('upload')}
            className={`px-3 py-1 rounded transition ${page === 'upload' ? 'bg-slate-700 text-white' : 'text-slate-400 hover:text-white'}`}
          >
            Upload
          </button>
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-slate-500">API Key</span>
          <input
            type="password"
            value={apiKey}
            onChange={handleKeyChange}
            placeholder="not required"
            className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-300 focus:outline-none focus:border-blue-500 w-40"
          />
        </div>
      </header>

      {/* Page content */}
      <main className="flex-1 flex flex-col">
        {page === 'upload' ? (
          <UploadPage onSessionReady={(id) => { setSessionId(id); setPage('chat') }} />
        ) : (
          <ChatPage sessionId={sessionId} onSessionChange={setSessionId} />
        )}
      </main>
    </div>
  )
}
