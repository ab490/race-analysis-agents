import Plot from 'react-plotly.js'

export default function PlotSection({ figure, caption }) {
  if (!figure || !figure.data) return null

  return (
    <div className="my-2">
      <Plot
        data={figure.data}
        layout={{
          ...figure.layout,
          autosize: true,
          paper_bgcolor: 'transparent',
          plot_bgcolor: '#1e2130',
          font: { color: '#e2e8f0' },
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
