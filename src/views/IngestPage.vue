<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile } from 'element-plus'
import JobCard from '@/components/ingest/JobCard.vue'
import { ApiError, ingestApi } from '@/ingest/api'
import { formatTime, humanBytes, percent } from '@/ingest/format'
import { useUploader } from '@/ingest/uploader'
import type { CollectionInfo, Job, JobAction, PayloadMode, ServiceStatus, UploadInfo } from '@/ingest/types'
import { logout } from '@/auth'

const router = useRouter()

const mode = ref<'url' | 'upload'>('url')
const linksText = ref('')
const name = ref('')
const payload = ref<PayloadMode>('all')
const indexAfter = ref(true)
const submitting = ref(false)
const formError = ref('')
const sessionExpired = ref(false)

const jobs = ref<Job[]>([])
const collections = ref<CollectionInfo[]>([])
const pendingUploads = ref<UploadInfo[]>([])
const service = ref<ServiceStatus | null>(null)
const loadError = ref('')
const busyJob = ref<string | null>(null)

const chunkSize = () => service.value?.limits.uploadChunkSize ?? 8 * 1024 * 1024
const uploader = useUploader(chunkSize)

let jobTimer: number | undefined
let statusTimer: number | undefined
let lastActive = false

const links = computed(() =>
  linksText.value
    .split(/\s+/)
    .map((item) => item.trim())
    .filter(Boolean),
)

const invalidLinks = computed(() => links.value.filter((item) => !/^https:\/\/[^\s/]+\/\S*$/i.test(item)))

const normalizedName = computed(() => name.value.trim().replace(/^arcp:\/\/name,/, ''))
const nameInvalid = computed(() => normalizedName.value !== '' && !/^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$/.test(normalizedName.value))
const existingMatch = computed(() =>
  normalizedName.value ? collections.value.find((item) => item.id === `arcp://name,${normalizedName.value}`) : undefined,
)

const uploadTotal = computed(() => uploader.items.reduce((sum, item) => sum + item.file.size, 0))
const uploadDone = computed(() =>
  uploader.items.reduce((sum, item) => sum + Math.min(item.file.size, item.received + item.inflight), 0),
)

const canSubmit = computed(() => {
  if (submitting.value || nameInvalid.value) return false
  if (mode.value === 'url') return links.value.length > 0 && invalidLinks.value.length === 0
  return uploader.items.length > 0 && !uploader.state.running
})

const activeJobs = computed(() => jobs.value.filter((job) => ['queued', 'running', 'deleting'].includes(job.status)))

const resumableUploads = computed(() =>
  pendingUploads.value.filter(
    (upload) => !upload.jobId && !uploader.items.some((item) => item.uploadId === upload.id),
  ),
)

function handleError(error: unknown, fallback: string): string {
  if (error instanceof ApiError && error.status === 401) {
    sessionExpired.value = true
    return 'Your session expired. Log in again to continue; running jobs keep going on the server.'
  }
  if (error instanceof ApiError && error.status === 403) return 'This account is not allowed to ingest data.'
  return error instanceof Error ? error.message : fallback
}

async function loadJobs(): Promise<void> {
  try {
    const result = await ingestApi.jobs()
    jobs.value = result.jobs
    loadError.value = ''
    const active = result.jobs.some((job) => ['queued', 'running', 'deleting'].includes(job.status))
    if (lastActive && !active) void loadCollections()
    lastActive = active
  } catch (error) {
    loadError.value = handleError(error, 'Jobs could not be loaded.')
  }
}

async function loadCollections(): Promise<void> {
  try {
    collections.value = (await ingestApi.collections()).collections
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) handleError(error, '')
  }
}

async function loadStatus(): Promise<void> {
  try {
    service.value = await ingestApi.status()
    pendingUploads.value = (await ingestApi.uploads()).uploads
  } catch (error) {
    loadError.value = handleError(error, 'The ingest service is not reachable.')
  }
}

function schedule(): void {
  window.clearTimeout(jobTimer)
  const fast = activeJobs.value.length > 0 || uploader.state.running
  jobTimer = window.setTimeout(async () => {
    if (!document.hidden) await loadJobs()
    schedule()
  }, fast ? 2000 : 10000)
}

async function submit(): Promise<void> {
  formError.value = ''
  submitting.value = true
  try {
    let uploadIds: string[] | undefined
    if (mode.value === 'upload') {
      schedule()
      const result = await uploader.run()
      if (!result) {
        formError.value = uploader.state.error
        return
      }
      uploadIds = result
    }
    await ingestApi.createJob({
      source: mode.value,
      urls: mode.value === 'url' ? links.value : undefined,
      uploadIds,
      name: normalizedName.value || null,
      payload: payload.value,
      index: indexAfter.value,
    })
    ElMessage.success('Ingestion job started.')
    if (mode.value === 'url') linksText.value = ''
    else uploader.clear()
    await Promise.all([loadJobs(), loadStatus()])
    schedule()
  } catch (error) {
    formError.value = handleError(error, 'The job could not be started.')
  } finally {
    submitting.value = false
  }
}

function onFileChange(file: UploadFile): void {
  if (file.raw) uploader.add([file.raw])
}

async function discardUpload(upload: UploadInfo): Promise<void> {
  try {
    await ElMessageBox.confirm(`Discard the partial upload of ${upload.filename}?`, 'Discard upload', {
      type: 'warning',
      confirmButtonText: 'Discard',
    })
  } catch {
    return
  }
  try {
    await ingestApi.deleteUpload(upload.id)
    await loadStatus()
  } catch (error) {
    ElMessage.error(handleError(error, 'The upload could not be discarded.'))
  }
}

async function onJobAction(action: JobAction, job: Job): Promise<void> {
  if (action === 'remove') {
    try {
      await ElMessageBox.confirm(
        'Remove this job and its working files? Data already imported into the repository stays.',
        'Remove job',
        { type: 'warning', confirmButtonText: 'Remove' },
      )
    } catch {
      return
    }
  }
  busyJob.value = job.id
  try {
    if (action === 'pause') await ingestApi.pauseJob(job.id)
    if (action === 'resume') await ingestApi.resumeJob(job.id)
    if (action === 'reindex') await ingestApi.reindexJob(job.id)
    if (action === 'remove') await ingestApi.removeJob(job.id)
    await loadJobs()
    schedule()
  } catch (error) {
    ElMessage.error(handleError(error, 'The action failed.'))
  } finally {
    busyJob.value = null
  }
}

function relogin(): void {
  logout()
  router.push('/login')
}

function uploadPercent(received: number, size: number): number {
  return percent(received, size)
}

function warnBeforeLeave(event: BeforeUnloadEvent): void {
  if (uploader.state.running) {
    event.preventDefault()
  }
}

watch(
  () => uploader.state.running,
  () => schedule(),
)

onMounted(async () => {
  window.addEventListener('beforeunload', warnBeforeLeave)
  await Promise.all([loadStatus(), loadJobs(), loadCollections()])
  schedule()
  statusTimer = window.setInterval(() => {
    if (!document.hidden) void loadStatus()
  }, 15000)
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', warnBeforeLeave)
  window.clearTimeout(jobTimer)
  window.clearInterval(statusTimer)
})
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <p class="eyebrow">Data ingestion</p>
        <h1>Ingest RO-Crate collections</h1>
        <p class="lead">
          Download zipped RO-Crates from portal links or upload them, then assemble, import into the OCFL repository and
          index them in the portal. Interrupted downloads and uploads resume automatically.
        </p>
      </div>
      <div v-if="service" class="health">
        <span class="pill" :class="service.labApi.reachable ? 'pill--ok' : 'pill--bad'">
          Portal API {{ service.labApi.reachable ? 'online' : 'unreachable' }}
        </span>
        <span class="pill" :class="service.ocfl.ready ? 'pill--ok' : 'pill--bad'">
          Repository {{ service.ocfl.ready ? 'ready' : 'unavailable' }}
        </span>
        <span class="pill">Usable space {{ humanBytes(service.disk.usable) }}</span>
      </div>
    </header>

    <el-alert v-if="sessionExpired" type="warning" show-icon :closable="false" class="page-alert">
      <template #title>Your session expired. Running jobs continue on the server.</template>
      <el-button size="small" type="primary" @click="relogin">Log in again</el-button>
    </el-alert>
    <el-alert v-else-if="loadError" type="error" :title="loadError" show-icon :closable="false" class="page-alert" />

    <div class="ingest-grid">
      <section class="panel" aria-labelledby="new-ingestion">
        <div class="panel-head">
          <h2 id="new-ingestion">New ingestion</h2>
        </div>

        <el-segmented
          v-model="mode"
          :options="[
            { label: 'From links', value: 'url' },
            { label: 'Upload files', value: 'upload' },
          ]"
          block
          size="large"
          class="mode-switch"
          :disabled="uploader.state.running || submitting"
        />

        <el-form label-position="top" class="ingest-form" @submit.prevent>
          <template v-if="mode === 'url'">
            <el-form-item label="Download links">
              <el-input
                v-model="linksText"
                type="textarea"
                :autosize="{ minRows: 4, maxRows: 10 }"
                placeholder="Paste one link per line, in part order (part 1, part 2, …)"
                spellcheck="false"
              />
              <p class="hint">
                {{ links.length }} link{{ links.length === 1 ? '' : 's' }}. Split parts (.zip.001 … or .z01 … .zip) are
                joined automatically; separate zips are merged into one crate folder.
              </p>
              <p v-if="invalidLinks.length" class="hint hint--error">
                {{ invalidLinks.length }} line{{ invalidLinks.length === 1 ? ' is' : 's are' }} not an https link.
              </p>
            </el-form-item>
          </template>

          <template v-else>
            <el-form-item label="Zip files">
              <el-upload
                class="dropzone"
                drag
                multiple
                :auto-upload="false"
                :show-file-list="false"
                :on-change="onFileChange"
                :disabled="uploader.state.running"
              >
                <div class="dropzone__text">
                  <strong>Drop zip files here</strong>
                  <span>or click to choose. Select every part of a split archive.</span>
                </div>
              </el-upload>
            </el-form-item>

            <ul v-if="uploader.items.length" class="files">
              <li v-for="(item, position) in uploader.items" :key="item.key" class="file">
                <div class="file__row">
                  <span class="file__name" :title="item.file.name">{{ item.file.name }}</span>
                  <span class="file__size">
                    {{ humanBytes(Math.min(item.file.size, item.received + item.inflight)) }} / {{ humanBytes(item.file.size) }}
                    <template v-if="item.speed && item.status === 'uploading'"> · {{ humanBytes(item.speed) }}/s</template>
                  </span>
                </div>
                <el-progress
                  :percentage="item.status === 'complete' ? 100 : uploadPercent(item.received + item.inflight, item.file.size)"
                  :stroke-width="6"
                  :show-text="false"
                  :status="item.status === 'complete' ? 'success' : item.status === 'error' ? 'exception' : item.status === 'retrying' ? 'warning' : undefined"
                />
                <div class="file__row file__row--meta">
                  <span class="file__status" :class="`file__status--${item.status}`">{{ item.message || item.status }}</span>
                  <span v-if="!uploader.state.running" class="file__tools">
                    <el-button link size="small" :disabled="position === 0" @click="uploader.move(item.key, -1)">Up</el-button>
                    <el-button
                      link
                      size="small"
                      :disabled="position === uploader.items.length - 1"
                      @click="uploader.move(item.key, 1)"
                    >
                      Down
                    </el-button>
                    <el-button link size="small" type="danger" @click="uploader.remove(item.key)">Remove</el-button>
                  </span>
                </div>
              </li>
            </ul>
            <p v-if="uploader.items.length" class="hint">
              {{ uploader.items.length }} file{{ uploader.items.length === 1 ? '' : 's' }},
              {{ humanBytes(uploadDone) }} of {{ humanBytes(uploadTotal) }} uploaded. Parts are ordered by name; adjust
              if needed.
            </p>

            <div v-if="resumableUploads.length" class="resume-box">
              <p class="resume-box__title">Interrupted uploads</p>
              <p class="hint">Select the same file again to continue where it stopped.</p>
              <ul>
                <li v-for="upload in resumableUploads" :key="upload.id">
                  <span class="file__name">{{ upload.filename }}</span>
                  <span class="file__size">{{ percent(upload.received, upload.size) }}% of {{ humanBytes(upload.size) }}</span>
                  <el-button link size="small" type="danger" @click="discardUpload(upload)">Discard</el-button>
                </li>
              </ul>
            </div>
          </template>

          <el-form-item label="Collection identifier (optional)" :error="nameInvalid ? 'Use letters, digits and . _ ~ - only.' : ''">
            <el-input v-model="name" placeholder="derived from the crate, e.g. alrc" clearable>
              <template #prepend>arcp://name,</template>
            </el-input>
            <p v-if="existingMatch" class="hint hint--info">
              This collection already exists (OCFL {{ existingMatch.head }}, {{ existingMatch.files }} files). Ingesting
              creates a new version and refreshes its portal records.
            </p>
            <p v-else class="hint">Leave empty to keep the crate's own identifier or derive one from its folder name.</p>
          </el-form-item>

          <el-form-item label="Files to store">
            <el-radio-group v-model="payload">
              <el-radio-button value="all">All files</el-radio-button>
              <el-radio-button value="text">Extracted text only</el-radio-button>
            </el-radio-group>
            <p class="hint">
              Text only keeps the metadata and text files (as used for the document collections) and needs far less space.
            </p>
          </el-form-item>

          <el-form-item>
            <el-switch v-model="indexAfter" active-text="Index in the portal after import" />
          </el-form-item>

          <el-alert v-if="formError" type="error" :title="formError" show-icon :closable="false" class="form-alert" />

          <div class="form-actions">
            <el-button
              type="primary"
              size="large"
              :loading="submitting"
              :disabled="!canSubmit"
              @click="submit"
            >
              {{ mode === 'url' ? 'Start download & ingest' : uploader.state.running ? 'Uploading…' : 'Upload & ingest' }}
            </el-button>
            <el-button v-if="mode === 'upload' && uploader.state.running" size="large" @click="uploader.pause()">
              Pause upload
            </el-button>
          </div>
        </el-form>
      </section>

      <section class="panel" aria-labelledby="jobs-title">
        <div class="panel-head">
          <h2 id="jobs-title">Ingestion jobs</h2>
          <el-button text @click="loadJobs">Refresh</el-button>
        </div>
        <el-empty v-if="!jobs.length" description="No ingestion jobs yet." :image-size="80" />
        <div v-else class="jobs">
          <JobCard v-for="job in jobs" :key="job.id" :job="job" :busy="busyJob === job.id" @action="onJobAction" />
        </div>
      </section>
    </div>

    <section class="panel" aria-labelledby="repository-title">
      <div class="panel-head">
        <h2 id="repository-title">Collections in the repository</h2>
        <el-button text @click="loadCollections">Refresh</el-button>
      </div>
      <el-table v-if="collections.length" :data="collections" size="small" class="collections">
        <el-table-column prop="id" label="Identifier" min-width="260">
          <template #default="{ row }"><code>{{ row.id }}</code></template>
        </el-table-column>
        <el-table-column prop="head" label="OCFL version" width="120" />
        <el-table-column prop="files" label="Files" width="90" />
        <el-table-column label="Updated" min-width="170">
          <template #default="{ row }">{{ formatTime(row.updated) }}</template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="The repository has no collections yet." :image-size="60" />
    </section>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.page-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
}

.eyebrow {
  margin: 0 0 4px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 0.75rem;
  color: var(--rapid-accent);
  font-weight: 700;
}

.page-head h1 {
  margin: 0;
  font-size: clamp(1.6rem, 2.6vw, 2.2rem);
  line-height: 1.15;
}

.lead {
  margin: 8px 0 0;
  max-width: 70ch;
  color: var(--rapid-muted);
}

.health {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 999px;
  background: var(--rapid-surface);
  border: 1px solid var(--rapid-border);
  font-size: 0.85rem;
}

.pill--ok::before,
.pill--bad::before {
  content: '';
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.pill--ok::before {
  background: #1f9d55;
}

.pill--bad::before {
  background: #d64545;
}

.page-alert :deep(.el-alert__description) {
  margin-top: 8px;
}

.ingest-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 24px;
  align-items: start;
}

@media (min-width: 1100px) {
  .ingest-grid {
    grid-template-columns: minmax(380px, 5fr) minmax(0, 7fr);
  }

  .ingest-grid > .panel:first-child {
    position: sticky;
    top: 88px;
  }
}

.panel {
  background: var(--rapid-surface);
  border: 1px solid var(--rapid-border);
  border-radius: var(--rapid-radius);
  box-shadow: var(--rapid-shadow);
  padding: 20px;
  min-width: 0;
}

@media (min-width: 640px) {
  .panel {
    padding: 24px;
  }
}

.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.panel-head h2 {
  margin: 0;
  font-size: 1.25rem;
}

.mode-switch {
  margin-bottom: 20px;
}

.ingest-form :deep(.el-form-item__label) {
  font-weight: 600;
}

.hint {
  margin: 6px 0 0;
  font-size: 0.82rem;
  line-height: 1.4;
  color: var(--rapid-muted);
}

.hint--error {
  color: #b42318;
}

.hint--info {
  color: var(--rapid-accent);
}

.dropzone {
  width: 100%;
}

.dropzone :deep(.el-upload-dragger) {
  border-radius: 12px;
  padding: 28px 16px;
  background: var(--rapid-brand-50);
  border-color: var(--rapid-brand-200);
}

.dropzone__text {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 0.95rem;
}

.dropzone__text span {
  color: var(--rapid-muted);
  font-size: 0.85rem;
}

.files {
  list-style: none;
  margin: 0 0 8px;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.file__row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  font-size: 0.85rem;
  margin-bottom: 4px;
}

.file__row--meta {
  margin: 4px 0 0;
}

.file__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.file__size {
  color: var(--rapid-muted);
  white-space: nowrap;
}

.file__status {
  color: var(--rapid-muted);
  font-size: 0.8rem;
}

.file__status--retrying,
.file__status--paused {
  color: #9a5b00;
}

.file__status--error {
  color: #b42318;
}

.file__status--complete {
  color: #1f7a3d;
}

.file__tools {
  white-space: nowrap;
}

.resume-box {
  margin: 8px 0 18px;
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px dashed var(--rapid-brand-200);
  background: #fffaf2;
}

.resume-box__title {
  margin: 0;
  font-weight: 700;
}

.resume-box ul {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.resume-box li {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 10px;
  align-items: center;
  font-size: 0.85rem;
}

.form-alert {
  margin-bottom: 16px;
}

.form-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.form-actions .el-button {
  flex: 1 1 200px;
  margin-left: 0;
}

.jobs {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.collections code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.85rem;
}
</style>
