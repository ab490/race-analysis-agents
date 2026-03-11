import { useEffect, useState } from 'react'
import { flushSync } from 'react-dom'
import Plot from 'react-plotly.js'

export default function PlotSection({ figure: rawFigure, caption, printing = false }) {
  const [localPrinting, setLocalPrinting] = useState(false)
  const isLight = printing || localPrinting

  // Handle right-click → Print (beforeprint fires before browser captures)
  useEffect(() => {
    const before = () => flushSync(() => setLocalPrinting(true))
    const after = () => setLocalPrinting(false)
    window.addEventListener('beforeprint', before)
    window.addEventListener('afterprint', after)
    return () => {
      window.removeEventListener('beforeprint', before)
      window.removeEventListener('afterprint', after)
    }
  }, [])

  // LLMs sometimes double-serialize the figure as a JSON string
  let figure = rawFigure
  if (typeof figure === 'string') {
    try { figure = JSON.parse(figure) } catch { return null }
  }
  if (!figure || !figure.data) return null

  return (
    <div className="my-2">
      <Plot
        data={figure.data}
        layout={{
          ...figure.layout,
          autosize: true,
          paper_bgcolor: isLight ? 'white' : 'transparent',
          plot_bgcolor: isLight ? '#f1f5f9' : '#1e2130',
          font: { color: isLight ? '#1e293b' : '#e2e8f0' },
          margin: { t: 40, r: 20, b: 50, l: 60 },
        }}
        style={{ width: '100%', minHeight: 380 }}
        useResizeHandler
        config={{ displayModeBar: true, responsive: true }}
      />
      {caption && (
        <p className="text-xs text-slate-400 mt-1 px-1">{caption}</p>
      )}
    </div>
  )
}
