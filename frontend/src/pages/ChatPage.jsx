import { useEffect, useRef, useState } from 'react'
import { askQuestion, getSessions } from '../api'
import ReportView from '../components/ReportView'

export default function ChatPage({ sessionId, onSessionChange }) {
  const [sessions, setSessions] = useState([])
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef(null)

  useEffect(() => {
    getSessions().then(setSessions).catch(() => {})
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function handleSubmit(e) {
    e.preventDefault()
    if (!input.trim() || !sessionId || loading) return

    const question = input.trim()
    setInput('')
    setMessages((prev) => [...prev, { role: 'user', text: question }])
    setLoading(true)

    try {
      const report = await askQuestion(sessionId, question)
      setMessages((prev) => [...prev, { role: 'assistant', report }])
    } catch (err) {
      const errMsg = err.response?.data?.detail || err.message
      setMessages((prev) => [...prev, { role: 'assistant', report: {
        title: 'Error',
        sections: [{ type: 'text', content: errMsg }],
      }}])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-53px)]">
      {/* Session selector bar */}
      <div className="border-b border-slate-800 px-4 py-2 flex items-center gap-3 bg-slate-900/50">
        <span className="text-xs text-slate-400 shrink-0">Session</span>
        <select
          value={sessionId || ''}
          onChange={(e) => { onSessionChange(e.target.value); setMessages([]) }}
          className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-slate-200 focus:outline-none focus:border-blue-500 flex-1 max-w-xs"
        >
          <option value="">— select a session —</option>
          {sessions.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        {sessionId && (
          <span className="text-xs text-green-400">● active</span>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-6 flex flex-col gap-6">
        {messages.length === 0 && !loading && (
          <div className="text-center text-slate-500 text-sm mt-20">
            {sessionId
              ? 'Ask anything about the session — stats, plots, lap comparisons.'
              : 'Select a session above to get started.'}
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            {msg.role === 'user' ? (
              <div className="bg-blue-600 text-white rounded-2xl rounded-tr-sm px-4 py-2 text-sm max-w-lg">
                {msg.text}
              </div>
            ) : (
              <div className="bg-slate-800 rounded-2xl rounded-tl-sm px-4 py-3 text-sm max-w-3xl w-full">
                <ReportView report={msg.report} />
              </div>
            )}
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="bg-slate-800 rounded-2xl rounded-tl-sm px-4 py-3 text-slate-400 text-sm">
              <span className="animate-pulse">Thinking…</span>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <form onSubmit={handleSubmit} className="border-t border-slate-800 px-4 py-3 flex gap-3 bg-slate-900/50">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={sessionId ? 'Ask a question…' : 'Select a session first'}
          disabled={!sessionId || loading}
          className="flex-1 bg-slate-800 border border-slate-700 rounded-xl px-4 py-2 text-sm focus:outline-none focus:border-blue-500 disabled:opacity-40"
        />
        <button
          type="submit"
          disabled={!sessionId || loading || !input.trim()}
          className="bg-blue-600 hover:bg-blue-700 disabled:opacity-40 text-white rounded-xl px-5 py-2 text-sm font-medium transition"
        >
          Send
        </button>
      </form>
    </div>
  )
}
