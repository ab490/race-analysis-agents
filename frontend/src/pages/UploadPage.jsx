import { useState } from 'react'
import { uploadSession, uploadTrack } from '../api'

export default function UploadPage({ onSessionReady }) {
  const [mode, setMode] = useState('session') // 'session' | 'track'

  // Session upload state
  const [trackId, setTrackId] = useState('')
  const [sessionFiles, setSessionFiles] = useState([])
  const [sessionStatus, setSessionStatus] = useState(null)
  const [sessionError, setSessionError] = useState(null)
  const [sessionLoading, setSessionLoading] = useState(false)

  // Track upload state
  const [newTrackId, setNewTrackId] = useState('')
  const [kmlFile, setKmlFile] = useState(null)
  const [segmentsFile, setSegmentsFile] = useState(null)
  const [trackStatus, setTrackStatus] = useState(null)
  const [trackError, setTrackError] = useState(null)
  const [trackLoading, setTrackLoading] = useState(false)

  async function handleSessionUpload(e) {
    e.preventDefault()
    setSessionError(null)
    setSessionStatus(null)
    setSessionLoading(true)
    try {
      const result = await uploadSession(trackId, sessionFiles)
      setSessionStatus(result)
    } catch (err) {
      setSessionError(err.response?.data?.detail || err.message)
    } finally {
      setSessionLoading(false)
    }
  }

  async function handleTrackUpload(e) {
    e.preventDefault()
    setTrackError(null)
    setTrackStatus(null)
    setTrackLoading(true)
    try {
      const result = await uploadTrack(newTrackId, kmlFile, segmentsFile)
      setTrackStatus(result)
    } catch (err) {
      setTrackError(err.response?.data?.detail || err.message)
    } finally {
      setTrackLoading(false)
    }
  }

  return (
    <div className="max-w-2xl mx-auto w-full p-8">
      <h1 className="text-2xl font-bold mb-6">Upload</h1>

      {/* Tab switcher */}
      <div className="flex gap-2 mb-8">
        {['session', 'track'].map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`px-4 py-2 rounded text-sm font-medium transition ${
              mode === m ? 'bg-blue-600 text-white' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
            }`}
          >
            {m === 'session' ? 'Session CSVs' : 'Track Files'}
          </button>
        ))}
      </div>

      {mode === 'session' && (
        <form onSubmit={handleSessionUpload} className="flex flex-col gap-4">
          <label className="flex flex-col gap-1">
            <span className="text-sm text-slate-400">Track ID</span>
            <input
              type="text"
              value={trackId}
              onChange={(e) => setTrackId(e.target.value)}
              placeholder="e.g. laguna_seca"
              required
              className="bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-sm text-slate-400">CSV Files (rosbag2 topics + *_stat.csv)</span>
            <input
              type="file"
              multiple
              accept=".csv"
              onChange={(e) => setSessionFiles(Array.from(e.target.files))}
              required
              className="text-sm text-slate-300 file:mr-3 file:py-1 file:px-3 file:rounded file:border-0 file:bg-slate-700 file:text-slate-200 file:cursor-pointer"
            />
            {sessionFiles.length > 0 && (
              <span className="text-xs text-slate-500">{sessionFiles.length} file(s) selected</span>
            )}
          </label>

          <button
            type="submit"
            disabled={sessionLoading}
            className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded px-4 py-2 text-sm font-medium transition"
          >
            {sessionLoading ? 'Uploading...' : 'Upload Session'}
          </button>

          {sessionError && (
            <div className="bg-red-900/40 border border-red-700 text-red-300 rounded px-4 py-3 text-sm">
              {sessionError}
            </div>
          )}

          {sessionStatus && (
            <div className="bg-green-900/40 border border-green-700 text-green-300 rounded px-4 py-3 text-sm space-y-1">
              <p className="font-medium">{sessionStatus.message}</p>
              <p>Session ID: <code className="text-green-200">{sessionStatus.session_id}</code></p>
              <p>Laps: {sessionStatus.lap_count} &nbsp;·&nbsp; Duration: {sessionStatus.duration_seconds}s</p>
              <button
                onClick={() => onSessionReady(sessionStatus.session_id)}
                className="mt-2 bg-green-700 hover:bg-green-600 text-white rounded px-3 py-1 text-xs font-medium transition"
              >
                Open in Chat →
              </button>
            </div>
          )}
        </form>
      )}

      {mode === 'track' && (
        <form onSubmit={handleTrackUpload} className="flex flex-col gap-4">
          <label className="flex flex-col gap-1">
            <span className="text-sm text-slate-400">Track ID</span>
            <input
              type="text"
              value={newTrackId}
              onChange={(e) => setNewTrackId(e.target.value)}
              placeholder="e.g. laguna_seca"
              required
              className="bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-sm text-slate-400">KML File (*_track.kml)</span>
            <input
              type="file"
              accept=".kml"
              onChange={(e) => setKmlFile(e.target.files[0])}
              required
              className="text-sm text-slate-300 file:mr-3 file:py-1 file:px-3 file:rounded file:border-0 file:bg-slate-700 file:text-slate-200 file:cursor-pointer"
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-sm text-slate-400">Segments CSV (*_segments.csv)</span>
            <input
              type="file"
              accept=".csv"
              onChange={(e) => setSegmentsFile(e.target.files[0])}
              required
              className="text-sm text-slate-300 file:mr-3 file:py-1 file:px-3 file:rounded file:border-0 file:bg-slate-700 file:text-slate-200 file:cursor-pointer"
            />
          </label>

          <button
            type="submit"
            disabled={trackLoading}
            className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded px-4 py-2 text-sm font-medium transition"
          >
            {trackLoading ? 'Uploading...' : 'Upload Track'}
          </button>

          {trackError && (
            <div className="bg-red-900/40 border border-red-700 text-red-300 rounded px-4 py-3 text-sm">
              {trackError}
            </div>
          )}

          {trackStatus && (
            <div className="bg-green-900/40 border border-green-700 text-green-300 rounded px-4 py-3 text-sm space-y-1">
              <p className="font-medium">{trackStatus.message}</p>
              <p>Segments: {trackStatus.segment_count}</p>
            </div>
          )}
        </form>
      )}
    </div>
  )
}
