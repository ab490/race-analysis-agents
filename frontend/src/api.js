import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

// Attach stored API key to every axios request
api.interceptors.request.use((config) => {
  const key = localStorage.getItem('api_key') || ''
  if (key) config.headers['X-API-Key'] = key
  return config
})

export function getStoredApiKey() {
  return localStorage.getItem('api_key') || ''
}

export function setStoredApiKey(key) {
  if (key) localStorage.setItem('api_key', key)
  else localStorage.removeItem('api_key')
}

export async function getSessions() {
  const { data } = await api.get('/upload/sessions')
  return data.sessions
}

export async function getTracks() {
  const { data } = await api.get('/tracks/')
  return data.tracks
}

export async function uploadTrack(trackId, kmlFile, segmentsFile) {
  const form = new FormData()
  form.append('track_id', trackId)
  form.append('kml_file', kmlFile)
  form.append('segments_file', segmentsFile)
  const { data } = await api.post('/upload/track', form)
  return data
}

export function uploadSession(trackId, files, force = false, { onStatus, onDone, onError }) {
  const form = new FormData()
  form.append('track_id', trackId)
  form.append('force', force ? 'true' : 'false')
  for (const f of files) form.append('files', f)

  const key = getStoredApiKey()
  const controller = new AbortController()

  fetch('/api/upload/session', {
    method: 'POST',
    headers: key ? { 'X-API-Key': key } : {},
    body: form,
    signal: controller.signal,
  }).then(async (response) => {
    if (!response.ok) {
      const err = await response.json().catch(() => ({}))
      onError(err.detail || `Upload failed (${response.status})`)
      return
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop()

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const event = JSON.parse(line.slice(6))
          if (event.type === 'status') onStatus(event.text)
          else if (event.type === 'done') onDone(event.result)
          else if (event.type === 'error') onError(event.text)
        } catch {}
      }
    }
  }).catch((err) => {
    if (err.name !== 'AbortError') onError(err.message)
  })

  return () => controller.abort()
}

export async function getSessionInfo(sessionId) {
  const { data } = await api.get(`/upload/sessions/${sessionId}`)
  return data
}

/**
 * Stream a question via SSE. Returns a cancel function.
 * onStatus(text) — called for each tool-call status update
 * onDone(report) — called with the final report dict
 * onError(text)  — called on error
 */
export function streamQuestion(sessionId, message, { onStatus, onDone, onError }) {
  const controller = new AbortController()

  const key = getStoredApiKey()
  fetch('/api/query/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(key ? { 'X-API-Key': key } : {}),
    },
    body: JSON.stringify({ session_id: sessionId, message }),
    signal: controller.signal,
  }).then(async (response) => {
    if (!response.ok) {
      const err = await response.json().catch(() => ({}))
      onError(err.detail || `Request failed (${response.status})`)
      return
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() // keep any incomplete line

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const event = JSON.parse(line.slice(6))
          if (event.type === 'status') onStatus(event.text)
          else if (event.type === 'done') onDone(event.report)
          else if (event.type === 'error') onError(event.text)
        } catch {}
      }
    }
  }).catch((err) => {
    if (err.name !== 'AbortError') onError(err.message)
  })

  return () => controller.abort()
}
