import { useEffect, useRef, useState } from 'react'
import { getSessionInfo, getSessions, streamQuestion } from '../api'
import ReportView from '../components/ReportView'

export default function ChatPage({ sessionId, onSessionChange }) {
  const [sessions, setSessions] = useState([])
  const [sessionMeta, setSessionMeta] = useState(null)
  const [messages, setMessages] = useState([])
  const [activeReport, setActiveReport] = useState(null)
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [statusText, setStatusText] = useState('')
  const bottomRef = useRef(null)
  const cancelStream = useRef(null)

  useEffect(() => {
    getSessions().then(setSessions).catch(() => {})
  }, [sessionId])

  useEffect(() => {
    if (!sessionId) { setSessionMeta(null); return }
    getSessionInfo(sessionId).then(setSessionMeta).catch(() => setSessionMeta(null))
  }, [sessionId])

  // Cancel any in-flight stream on unmount
  useEffect(() => () => cancelStream.current?.(), [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  function handleSubmit(e) {
    e.preventDefault()
    if (!input.trim() || !sessionId || loading) return

    const question = input.trim()
    setInput('')
    setStatusText('')
    setMessages((prev) => [...prev, { role: 'user', text: question }])
    setLoading(true)

    cancelStream.current = streamQuestion(sessionId, question, {
      onStatus: (text) => setStatusText(text),
      onDone: (report) => {
        setMessages((prev) => [...prev, { role: 'assistant', report }])
        setActiveReport(report)
        setLoading(false)
        setStatusText('')
      },
      onError: (text) => {
        const report = { title: 'Error', sections: [{ type: 'text', content: text }] }
        setMessages((prev) => [...prev, { role: 'assistant', report }])
        setActiveReport(report)
        setLoading(false)
        setStatusText('')
      },
    })
  }

  const lapCount = sessionMeta?.lap_boundaries?.filter(b => b.lap > 0).length ?? 0

  return (
    <div className="flex h-[calc(100vh-53px)]">

      {/* ── Left panel: conversation ── */}
      <div className="w-80 shrink-0 flex flex-col border-r border-slate-800 bg-slate-900/40">

        {/* Session selector */}
        <div className="px-3 py-3 border-b border-slate-800 flex flex-col gap-2">
          <select
            value={sessionId || ''}
            onChange={(e) => {
            cancelStream.current?.()
            setLoading(false)
            setStatusText('')
            onSessionChange(e.target.value)
            setMessages([])
            setActiveReport(null)
          }}
            className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
          >
            <option value="">— select a session —</option>
            {sessions.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          {sessionId && (
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <span className="text-green-400">● active</span>
              {lapCount > 0 && <span>{lapCount} lap{lapCount !== 1 ? 's' : ''}</span>}
            </div>
          )}
        </div>

        {/* Message history */}
        <div className="flex-1 overflow-y-auto px-3 py-4 flex flex-col gap-3">
          {messages.length === 0 && !loading && (
            <p className="text-xs text-slate-500 text-center mt-8">
              {sessionId ? 'Ask anything about the session.' : 'Select a session to get started.'}
            </p>
          )}

          {messages.map((msg, i) => (
            <div key={i} className={`flex flex-col gap-1 ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
              {msg.role === 'user' ? (
                <div className="bg-blue-600 text-white rounded-2xl rounded-tr-sm px-3 py-2 text-xs max-w-[95%]">
                  {msg.text}
                </div>
              ) : (
                <button
                  onClick={() => setActiveReport(msg.report)}
                  className={`text-left rounded-2xl rounded-tl-sm px-3 py-2 text-xs max-w-[95%] transition border ${
                    activeReport === msg.report
                      ? 'bg-slate-700 border-blue-500 text-slate-200'
                      : 'bg-slate-800 border-transparent text-slate-400 hover:border-slate-600 hover:text-slate-300'
                  }`}
                >
                  {msg.report?.title || 'Response'}
                </button>
              )}
            </div>
          ))}

          {loading && (
            <div className="flex items-start gap-2">
              <div className="bg-slate-800 rounded-2xl rounded-tl-sm px-3 py-2 text-xs text-slate-500">
                <span className="animate-pulse">{statusText || 'Thinking…'}</span>
              </div>
              <button
                onClick={() => {
                  cancelStream.current?.()
                  setLoading(false)
                  setStatusText('')
                }}
                className="text-xs text-slate-500 hover:text-slate-300 mt-1.5 transition"
              >
                Cancel
              </button>
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <form onSubmit={handleSubmit} className="border-t border-slate-800 p-3 flex flex-col gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSubmit(e) } }}
            placeholder={sessionId ? 'Ask a question…' : 'Select a session first'}
            disabled={!sessionId || loading}
            rows={2}
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500 disabled:opacity-40 resize-none"
          />
          <button
            type="submit"
            disabled={!sessionId || loading || !input.trim()}
            className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-40 text-white rounded-lg py-1.5 text-sm font-medium transition"
          >
            Send
          </button>
        </form>
      </div>

      {/* ── Right panel: report viewer ── */}
      <div className="flex-1 overflow-y-auto bg-[#0f1117]">
        {activeReport ? (
          <div className="max-w-4xl mx-auto px-8 py-8">
            <ReportView report={activeReport} />
          </div>
        ) : (
          <div className="h-full flex items-center justify-center text-slate-600 text-sm">
            {sessionId ? 'Ask a question to see the report here.' : 'Select a session and ask a question.'}
          </div>
        )}
      </div>

    </div>
  )
}
