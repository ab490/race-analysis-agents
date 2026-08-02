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

// Lightweight check against a real authenticated endpoint - resolves true only
// if the stored key (or no key, when auth is disabled server-side) is accepted.
export async function validateApiKey() {
  try {
    await api.get('/tracks/')
    return true
  } catch {
    return false
  }
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

// Max simultaneous direct-to-GCS uploads.
const UPLOAD_CONCURRENCY = 6

async function uploadFilesToGcs(files, urls, signal, onProgress) {
  const queue = files.map((f) => f) // shallow copy we can shift() from
  let completed = 0

  async function worker() {
    while (queue.length) {
      const file = queue.shift()
      const url = urls[file.name]
      if (!url) throw new Error(`No upload URL returned for ${file.name}`)
      const res = await fetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': 'text/csv' }, // must match the signed content-type
        body: file,
        signal,
      })
      if (!res.ok) throw new Error(`Direct upload failed for ${file.name} (${res.status})`)
      completed += 1
      onProgress(completed, files.length)
    }
  }

  const workers = Array.from({ length: Math.min(UPLOAD_CONCURRENCY, files.length) }, worker)
  await Promise.all(workers)
}

async function readSseStream(response, { onStatus, onDone, onError }) {
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
}

/**
 * Upload a session by pushing each CSV directly to GCS (bypassing Cloud Run's
 * 32 MiB request limit), then triggering server-side processing over SSE.
 * `force` is retained for API compatibility; the pipeline always rebuilds the
 * processed session from the raw files currently in storage.
 */
export function uploadSession(trackId, files, force = false, { onStatus, onDone, onError }) {
  const controller = new AbortController()

  ;(async () => {
    try {
      // 1. Ask the backend for a signed upload URL per file.
      onStatus('Requesting upload URLs…')
      const filenames = files.map((f) => f.name)
      const { data } = await api.post('/upload/signed-urls', { track_id: trackId, filenames })
      const { session_id, urls } = data

      // 2. Upload every file straight to GCS, in parallel.
      onStatus(`Uploading 0/${files.length} files…`)
      await uploadFilesToGcs(files, urls, controller.signal, (done, total) => {
        onStatus(`Uploading ${done}/${total} files…`)
      })

      // 3. Trigger processing and stream progress.
      onStatus('Processing session…')
      const key = getStoredApiKey()
      const response = await fetch('/api/upload/process', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(key ? { 'X-API-Key': key } : {}) },
        body: JSON.stringify({ track_id: trackId, session_id, force }),
        signal: controller.signal,
      })
      await readSseStream(response, { onStatus, onDone, onError })
    } catch (err) {
      if (err.name !== 'AbortError') {
        onError(err.response?.data?.detail || err.message)
      }
    }
  })()

  return () => controller.abort()
}

export async function getSessionInfo(sessionId) {
  const { data } = await api.get(`/upload/sessions/${sessionId}`)
  return data
}

/**
 * Stream a question via SSE. Returns a cancel function.
 * onStatus(text) - called for each tool-call status update
 * onDone(report) - called with the final report dict
 * onError(text)  - called on error
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
