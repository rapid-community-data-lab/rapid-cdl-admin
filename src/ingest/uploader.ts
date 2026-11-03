import { reactive } from 'vue'
import { ApiError, ingestApi } from './api'
import { naturalCompare } from './format'

export type UploadItemStatus = 'waiting' | 'uploading' | 'retrying' | 'paused' | 'complete' | 'error'

export interface UploadItem {
  key: string
  file: File
  uploadId: string | null
  received: number
  inflight: number
  speed: number
  status: UploadItemStatus
  message: string
}

const FATAL = new Set([401, 403, 404, 413, 507])

export function fingerprint(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function waitForRetry(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const finish = () => {
      window.clearTimeout(timer)
      window.removeEventListener('online', finish)
      signal.removeEventListener('abort', stop)
      resolve()
    }
    const stop = () => {
      window.clearTimeout(timer)
      window.removeEventListener('online', finish)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    const timer = window.setTimeout(finish, milliseconds)
    window.addEventListener('online', finish, { once: true })
    signal.addEventListener('abort', stop, { once: true })
  })
}

export function useUploader(chunkSize: () => number) {
  const items = reactive<UploadItem[]>([])
  const state = reactive({ running: false, error: '' })
  let controller: AbortController | null = null

  function add(files: File[]): void {
    for (const file of files) {
      const key = fingerprint(file)
      if (items.some((item) => item.key === key)) continue
      items.push({
        key,
        file,
        uploadId: null,
        received: 0,
        inflight: 0,
        speed: 0,
        status: 'waiting',
        message: '',
      })
    }
    items.sort((a, b) => naturalCompare(a.file.name, b.file.name))
  }

  function remove(key: string): void {
    const index = items.findIndex((item) => item.key === key)
    if (index >= 0 && !state.running) items.splice(index, 1)
  }

  function move(key: string, delta: number): void {
    const index = items.findIndex((item) => item.key === key)
    const target = index + delta
    if (index < 0 || target < 0 || target >= items.length || state.running) return
    const [item] = items.splice(index, 1)
    if (item) items.splice(target, 0, item)
  }

  function clear(): void {
    if (!state.running) items.splice(0, items.length)
  }

  async function uploadOne(item: UploadItem, signal: AbortSignal): Promise<void> {
    const session = await ingestApi.createUpload(item.file.name, item.file.size, item.key)
    item.uploadId = session.id
    item.received = session.received
    if (session.received > 0 && session.received < item.file.size) {
      item.message = `Resuming from ${Math.floor((session.received / item.file.size) * 100)}%`
    }
    let attempt = 0
    let windowStart = performance.now()
    let windowBytes = item.received
    while (item.received < item.file.size) {
      if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
      const end = Math.min(item.file.size, item.received + chunkSize())
      item.status = 'uploading'
      try {
        const result = await ingestApi.sendChunk(
          session.id,
          item.file.slice(item.received, end),
          item.received,
          (loaded) => {
            item.inflight = loaded
          },
          signal,
        )
        item.received = result.received
        item.inflight = 0
        item.message = ''
        attempt = 0
        const now = performance.now()
        if (now - windowStart >= 1000) {
          item.speed = ((item.received - windowBytes) / (now - windowStart)) * 1000
          windowStart = now
          windowBytes = item.received
        }
      } catch (error) {
        item.inflight = 0
        if (isAbort(error)) throw error
        if (error instanceof ApiError && error.status === 409 && typeof error.offset === 'number') {
          const stalled = error.offset === item.received
          item.received = error.offset
          if (stalled) await waitForRetry(1000, signal)
          continue
        }
        if (error instanceof ApiError && FATAL.has(error.status)) {
          item.status = 'error'
          item.message =
            error.status === 401
              ? 'Your session expired. Log in again, then select the same file to resume.'
              : error.message
          throw error
        }
        attempt += 1
        const delay = Math.min(30, 2 ** Math.min(attempt, 5))
        item.status = 'retrying'
        item.speed = 0
        item.message = `Connection problem; resuming in ${delay} s`
        await waitForRetry(delay * 1000, signal)
        try {
          const current = await ingestApi.getUpload(session.id)
          item.received = current.received
        } catch (refreshError) {
          if (refreshError instanceof ApiError && FATAL.has(refreshError.status)) {
            item.status = 'error'
            item.message = refreshError.message
            throw refreshError
          }
        }
      }
    }
    item.status = 'complete'
    item.speed = 0
    item.message = ''
  }

  async function run(): Promise<string[] | null> {
    if (state.running || !items.length) return null
    state.running = true
    state.error = ''
    controller = new AbortController()
    const signal = controller.signal
    try {
      for (const item of items) {
        if (item.status === 'complete' && item.uploadId) continue
        await uploadOne(item, signal)
      }
      return items.map((item) => item.uploadId as string)
    } catch (error) {
      if (isAbort(error)) {
        for (const item of items) {
          if (item.status !== 'complete' && item.status !== 'error') {
            item.status = 'paused'
            item.message = 'Paused'
            item.speed = 0
          }
        }
        return null
      }
      state.error = error instanceof Error ? error.message : 'Upload failed.'
      return null
    } finally {
      state.running = false
      controller = null
    }
  }

  function pause(): void {
    controller?.abort()
  }

  return { items, state, add, remove, move, clear, run, pause }
}
