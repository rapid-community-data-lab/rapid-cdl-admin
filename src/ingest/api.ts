import { getToken } from '@/auth'
import type {
  CollectionInfo,
  CreateJobBody,
  Job,
  ServiceStatus,
  UploadInfo,
} from './types'

const configuredBase = import.meta.env.VITE_INGEST_API_BASE_URL as string | undefined
const base = (configuredBase && configuredBase.trim() ? configuredBase : '/ingest-api').replace(/\/+$/, '')

export class ApiError extends Error {
  status: number
  offset?: number

  constructor(message: string, status: number, offset?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.offset = offset
  }
}

interface ErrorBody {
  detail?: unknown
  offset?: unknown
}

function parseBody(text: string): unknown {
  if (!text) return {}
  try {
    return JSON.parse(text)
  } catch {
    return {}
  }
}

function describe(body: unknown, fallback: string): { message: string; offset?: number } {
  const data = (body ?? {}) as ErrorBody
  let message = fallback
  if (typeof data.detail === 'string') {
    message = data.detail
  } else if (Array.isArray(data.detail) && data.detail.length) {
    const first = data.detail[0] as { msg?: string }
    message = first?.msg ?? fallback
  }
  return { message, offset: typeof data.offset === 'number' ? data.offset : undefined }
}

function authHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return headers
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${base}${path}`, { ...init, headers: authHeaders(init.headers) })
  } catch {
    throw new ApiError('The ingest service cannot be reached. Check the connection.', 0)
  }
  const text = await response.text()
  const body = parseBody(text)
  if (!response.ok) {
    const { message, offset } = describe(body, `Request failed (HTTP ${response.status})`)
    throw new ApiError(message, response.status, offset)
  }
  return body as T
}

function json(method: string, payload: unknown): RequestInit {
  return { method, body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' } }
}

export const ingestApi = {
  status: () => request<ServiceStatus>('/status'),
  collections: () => request<{ collections: CollectionInfo[] }>('/collections'),
  jobs: (limit = 30) => request<{ jobs: Job[] }>(`/jobs?limit=${limit}`),
  createJob: (body: CreateJobBody) => request<Job>('/jobs', json('POST', body)),
  pauseJob: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}/pause`, { method: 'POST' }),
  resumeJob: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}/resume`, { method: 'POST' }),
  reindexJob: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}/reindex`, { method: 'POST' }),
  removeJob: (id: string) => request<void>(`/jobs/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  uploads: () => request<{ uploads: UploadInfo[] }>('/uploads'),
  createUpload: (filename: string, size: number, fingerprint: string) =>
    request<UploadInfo>('/uploads', json('POST', { filename, size, fingerprint })),
  getUpload: (id: string) => request<UploadInfo>(`/uploads/${encodeURIComponent(id)}`),
  deleteUpload: (id: string) => request<void>(`/uploads/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  sendChunk(
    id: string,
    chunk: Blob,
    offset: number,
    onProgress: (loaded: number) => void,
    signal: AbortSignal,
  ): Promise<UploadInfo> {
    return new Promise((resolve, reject) => {
      if (signal.aborted) {
        reject(new DOMException('Aborted', 'AbortError'))
        return
      }
      const xhr = new XMLHttpRequest()
      const abort = () => xhr.abort()
      xhr.open('PATCH', `${base}/uploads/${encodeURIComponent(id)}`)
      const token = getToken()
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
      xhr.setRequestHeader('Upload-Offset', String(offset))
      xhr.setRequestHeader('Content-Type', 'application/offset+octet-stream')
      xhr.timeout = 10 * 60 * 1000
      xhr.upload.onprogress = (event) => onProgress(event.loaded)
      xhr.onload = () => {
        signal.removeEventListener('abort', abort)
        const body = parseBody(xhr.responseText)
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(body as UploadInfo)
          return
        }
        const { message, offset: serverOffset } = describe(body, `Upload failed (HTTP ${xhr.status})`)
        reject(new ApiError(message, xhr.status, serverOffset))
      }
      xhr.onerror = () => {
        signal.removeEventListener('abort', abort)
        reject(new ApiError('The connection dropped while uploading.', 0))
      }
      xhr.ontimeout = () => {
        signal.removeEventListener('abort', abort)
        reject(new ApiError('The upload timed out.', 0))
      }
      xhr.onabort = () => {
        signal.removeEventListener('abort', abort)
        reject(new DOMException('Aborted', 'AbortError'))
      }
      signal.addEventListener('abort', abort, { once: true })
      xhr.send(chunk)
    })
  },
}
