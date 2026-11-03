import type { JobStatus, StageName } from './types'

export function humanBytes(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let unit = 0
  while (Math.abs(size) >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return unit === 0 ? `${size.toFixed(0)} ${units[unit]}` : `${size.toFixed(1)} ${units[unit]}`
}

export function percent(done: number, total: number | null | undefined): number {
  if (!total || total <= 0) return 0
  return Math.max(0, Math.min(100, Math.floor((done / total) * 100)))
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function clock(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function naturalCompare(a: string, b: string): number {
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' })
}

export const stageLabels: Record<StageName, string> = {
  fetch: 'Download',
  assemble: 'Assemble',
  extract: 'Extract',
  prepare: 'Prepare crate',
  import: 'Import to OCFL',
  index: 'Index',
  cleanup: 'Clean up',
}

export const statusLabels: Record<JobStatus, string> = {
  queued: 'Queued',
  running: 'Running',
  paused: 'Paused',
  failed: 'Failed',
  completed: 'Completed',
  deleting: 'Removing',
}

export const statusTypes: Record<JobStatus, 'info' | 'primary' | 'warning' | 'danger' | 'success'> = {
  queued: 'info',
  running: 'primary',
  paused: 'warning',
  failed: 'danger',
  completed: 'success',
  deleting: 'info',
}
