import { useEffect, useState } from 'react'
import { getStoredApiKey, setStoredApiKey, validateApiKey } from './api'
import ChatPage from './pages/ChatPage'
import UploadPage from './pages/UploadPage'

export default function App() {
  const [page, setPage] = useState('chat') // 'chat' | 'upload'
  const [sessionId, setSessionId] = useState(null)
  const [apiKey, setApiKey] = useState(getStoredApiKey)
  const [keyInput, setKeyInput] = useState(getStoredApiKey)
  const [showKey, setShowKey] = useState(false)
  const [authStatus, setAuthStatus] = useState('checking') // 'checking' | 'ok' | 'fail'

  // Auto-login once on load if a key is already stored from a previous visit.
  useEffect(() => {
    validateApiKey().then((ok) => setAuthStatus(ok ? 'ok' : 'fail'))
  }, [])

  function handleKeyChange(e) {
    const val = e.target.value
    setApiKey(val)
    setStoredApiKey(val)
  }

  function handleGateSubmit(e) {
    e.preventDefault()
    setApiKey(keyInput)
    setStoredApiKey(keyInput)
    setAuthStatus('checking')
    validateApiKey().then((ok) => setAuthStatus(ok ? 'ok' : 'fail'))
  }

  if (authStatus !== 'ok') {
    return (
      <div className="min-h-screen bg-[#0f1117] text-slate-100 flex items-center justify-center p-6">
        <form onSubmit={handleGateSubmit} className="max-w-sm w-full flex flex-col items-center gap-4 text-center">
          <span className="font-bold text-xl tracking-tight text-white">Race Analysis</span>
          {authStatus === 'checking' ? (
            <p className="text-sm text-slate-400">Checking access…</p>
          ) : (
            <>
              <p className="text-sm text-slate-400">Enter your API key to continue.</p>
              <div className="w-full flex gap-2">
                <input
                  type={showKey ? 'text' : 'password'}
                  value={keyInput}
                  onChange={(e) => setKeyInput(e.target.value)}
                  placeholder="API key"
                  autoFocus
                  className="flex-1 bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-slate-200 text-center focus:outline-none focus:border-blue-500"
                />
                <button
                  type="button"
                  onClick={() => setShowKey((v) => !v)}
                  className="shrink-0 bg-slate-800 border border-slate-700 rounded px-3 text-xs text-slate-400 hover:text-white transition"
                >
                  {showKey ? 'Hide' : 'Show'}
                </button>
              </div>
              <button
                type="submit"
                className="w-full bg-blue-600 hover:bg-blue-700 text-white rounded px-4 py-2 text-sm font-medium transition"
              >
                Submit
              </button>
              {apiKey && (
                <p className="text-xs text-red-400">Invalid API key.</p>
              )}
            </>
          )}
        </form>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-100 flex flex-col">
      {/* Navbar */}
      <header className="print:hidden border-b border-slate-800 px-6 py-3 flex items-center gap-6">
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
            placeholder="enter API key"
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
