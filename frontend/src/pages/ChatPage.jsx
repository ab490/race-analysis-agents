import { useEffect, useRef, useState } from 'react'
import { getSessionInfo, getSessions, streamQuestion } from '../api'
import ReportView from '../components/ReportView'

export default function ChatPage({ sessionId, onSessionChange }) {
  const [sessions, setSessions] = useState([])
  const [sessionMeta, setSessionMeta] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [statusText, setStatusText] = useState('')
  const leftBottomRef = useRef(null)
  const rightBottomRef = useRef(null)
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
    leftBottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    rightBottomRef.current?.scrollIntoView({ behavior: 'smooth' })
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
        setLoading(false)
        setStatusText('')
      },
      onError: (text) => {
        const report = { title: 'Error', sections: [{ type: 'text', content: text }] }
        setMessages((prev) => [...prev, { role: 'assistant', report }])
        setLoading(false)
        setStatusText('')
      },
    })
  }

  const lapCount = sessionMeta?.lap_boundaries?.filter(b => b.lap > 0).length ?? 0

  return (
    <div className="flex h-[calc(100vh-53px)]">

      {/* ── Left panel: conversation ── */}
      <div className="print:hidden w-80 shrink-0 flex flex-col border-r border-slate-800 bg-slate-900/40">

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
          }}
            className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
          >
            <option value="">- select a session -</option>
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
                <div className="bg-slate-800 rounded-2xl rounded-tl-sm px-3 py-2 text-xs max-w-[95%] text-slate-400">
                  {msg.report?.title || 'Response'}
                </div>
              )}
            </div>
          ))}

          {loading && (
            <div className="flex items-start gap-2">
              <div className="bg-slate-800 rounded-2xl rounded-tl-sm px-3 py-2 text-xs text-slate-500">
                <span className="animate-pulse">Working…</span>
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

          <div ref={leftBottomRef} />
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

      {/* ── Right panel: scrollable report history ── */}
      <div className="flex-1 overflow-y-auto print:overflow-visible print:h-auto bg-[#0f1117] relative">

        {/* Download PDF button */}
        {messages.some(m => m.role === 'assistant') && (
          <button
            onClick={() => window.print()}
            className="print:hidden absolute top-4 right-6 z-10 flex items-center gap-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 hover:text-white text-xs px-3 py-1.5 rounded-lg transition"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M3 17a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm3.293-7.707a1 1 0 011.414 0L9 10.586V3a1 1 0 112 0v7.586l1.293-1.293a1 1 0 111.414 1.414l-3 3a1 1 0 01-1.414 0l-3-3a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
            Export PDF
          </button>
        )}

        {messages.filter(m => m.role === 'assistant').length === 0 && !loading ? (
          <div className="h-full flex items-center justify-center text-slate-600 text-sm">
            {sessionId ? 'Ask a question to see the report here.' : 'Select a session and ask a question.'}
          </div>
        ) : (
          <div id="print-report" className="max-w-4xl mx-auto px-8 py-8 flex flex-col gap-12">
            {messages.reduce((acc, msg, i) => {
              if (msg.role === 'user') {
                acc.push({ question: msg.text, report: null, key: i })
              } else if (msg.role === 'assistant' && acc.length > 0) {
                acc[acc.length - 1].report = msg.report
              }
              return acc
            }, []).map((pair) => (
              <div key={pair.key} className="flex flex-col gap-4">
                <div className="flex items-start gap-3">
                  <span className="shrink-0 mt-0.5 w-5 h-5 rounded-full bg-blue-600 flex items-center justify-center text-white text-[10px] font-bold">Q</span>
                  <p className="text-slate-300 text-sm font-medium">{pair.question}</p>
                </div>
                {pair.report && <ReportView report={pair.report} />}
              </div>
            ))}
            {loading && (
              <div className="flex items-center gap-3 text-slate-500 text-sm">
                <span className="w-5 h-5 rounded-full bg-slate-700 flex items-center justify-center text-[10px] font-bold shrink-0">A</span>
                <span className="animate-pulse">{statusText || 'Thinking…'}</span>
              </div>
            )}
            <div ref={rightBottomRef} />
          </div>
        )}
      </div>

    </div>
  )
}
