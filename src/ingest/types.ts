export type JobStatus = 'queued' | 'running' | 'paused' | 'failed' | 'completed' | 'deleting'
export type StageName = 'fetch' | 'assemble' | 'extract' | 'prepare' | 'import' | 'index' | 'cleanup'
export type StageStatus = 'pending' | 'running' | 'done' | 'failed' | 'paused' | 'skipped'
export type PayloadMode = 'all' | 'text'

export interface StageState {
  status: StageStatus
  done: number
  total: number | null
  message: string
  startedAt?: string
  finishedAt?: string
}

export interface JobPart {
  index: number
  url: string | null
  uploadId: string | null
  filename: string | null
  size: number | null
  received: number
  status: 'pending' | 'checking' | 'downloading' | 'retrying' | 'done'
  speed: number
  attempts: number
  message: string
  ranges: boolean | null
  removed: boolean
}

export interface CrateImport {
  version: string
  created: boolean
  changed: boolean
  added: number
  reused: number
  files: number
  bytes: number
}

export interface CrateIndex {
  status: 'running' | 'indexed' | 'settled' | 'timeout'
  members: number
  expected: number
  root?: boolean
  verified?: number
  parents?: number
  elapsed?: number
}

export interface CrateSummary {
  rootId: string
  previousRootId: string
  title: string
  changes: string[]
  warnings: string[]
  counts: { collections: number; objects: number; files: number; other: number; skipped: number }
  expectedMembers: number
  filesLocal: number
  filesPresent: number
  missingCount: number
  existing: { id: string; head: string; versions: number; files: number; updated: string | null } | null
  import: CrateImport | null
  index: CrateIndex | null
}

export interface LogEntry {
  t: string
  level: 'info' | 'warn' | 'error'
  msg: string
}

export interface Job {
  id: string
  createdAt: string
  updatedAt: string
  source: 'url' | 'upload'
  options: { name: string | null; payload: PayloadMode; index: boolean }
  status: JobStatus
  stage: StageName | 'done'
  error: string | null
  parts: JobPart[]
  stages: Record<StageName, StageState>
  assembly: { kind: string; groups: string[][] } | null
  extract: { files: number; bytes: number; skipped: number; roots: string[] } | null
  crates: CrateSummary[]
  log: LogEntry[]
  queuePosition: number | null
}

export interface UploadInfo {
  id: string
  filename: string
  size: number
  received: number
  status: 'uploading' | 'complete' | 'attached'
  createdAt: string
  updatedAt: string
  jobId: string | null
}

export interface ServiceStatus {
  disk: { total: number; free: number; reserve: number; usable: number }
  current: string | null
  queued: number
  labApi: { reachable: boolean }
  ocfl: { ready: boolean; message: string }
  limits: { uploadChunkSize: number; maxUploadBytes: number; maxUrls: number }
}

export interface CollectionInfo {
  id: string
  head: string
  files: number
  updated: string | null
}

export interface CreateJobBody {
  source: 'url' | 'upload'
  urls?: string[]
  uploadIds?: string[]
  name?: string | null
  payload: PayloadMode
  index: boolean
}

export type JobAction = 'pause' | 'resume' | 'reindex' | 'remove'
