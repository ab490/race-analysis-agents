import { useState } from 'react'
import UploadPage from './pages/UploadPage'
import ChatPage from './pages/ChatPage'

export default function App() {
  const [page, setPage] = useState('chat') // 'chat' | 'upload'
  const [sessionId, setSessionId] = useState(null)

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
