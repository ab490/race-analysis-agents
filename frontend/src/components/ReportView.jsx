import PlotSection from './PlotSection'

export default function ReportView({ report }) {
  if (!report) return null

  return (
    <div className="flex flex-col gap-3">
      {report.title && (
        <h3 className="font-semibold text-slate-200">{report.title}</h3>
      )}
      {(report.sections || []).map((section, i) => {
        if (section.type === 'text') {
          return (
            <p key={i} className="text-slate-300 text-sm leading-relaxed whitespace-pre-wrap">
              {section.content}
            </p>
          )
        }
        if (section.type === 'plot') {
          return <PlotSection key={i} figure={section.figure} caption={section.caption} />
        }
        return null
      })}
    </div>
  )
}
