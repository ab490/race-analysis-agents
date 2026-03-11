import Markdown from 'react-markdown'
import PlotSection from './PlotSection'

export default function ReportView({ report, printing = false }) {
  if (!report) return null

  return (
    <div className="flex flex-col gap-3">
      {report.title && (
        <h3 className="font-semibold text-slate-200">{report.title}</h3>
      )}
      {(report.sections || []).map((section, i) => {
        if (section.type === 'text') {
          return (
            <div key={i} className="text-slate-300 text-sm leading-relaxed prose prose-invert prose-sm max-w-none">
              <Markdown>{section.content}</Markdown>
            </div>
          )
        }
        if (section.type === 'plot') {
          return <PlotSection key={i} figure={section.figure} caption={section.caption} printing={printing} />
        }
        return null
      })}
    </div>
  )
}
