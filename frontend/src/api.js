import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export async function getSessions() {
  const { data } = await api.get('/upload/sessions')
  return data.sessions
}

export async function uploadTrack(trackId, kmlFile, segmentsFile) {
  const form = new FormData()
  form.append('track_id', trackId)
  form.append('kml_file', kmlFile)
  form.append('segments_file', segmentsFile)
  const { data } = await api.post('/upload/track', form)
  return data
}

export async function uploadSession(trackId, files) {
  const form = new FormData()
  form.append('track_id', trackId)
  for (const f of files) form.append('files', f)
  const { data } = await api.post('/upload/session', form)
  return data
}

export async function askQuestion(sessionId, message) {
  const { data } = await api.post('/query/ask', { session_id: sessionId, message })
  return data
}
