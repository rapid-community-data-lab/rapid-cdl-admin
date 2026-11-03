<script setup lang="ts">
import { computed } from 'vue'
import type { Job, JobAction, StageName } from '@/ingest/types'
import {
  clock,
  formatTime,
  humanBytes,
  percent,
  stageLabels,
  statusLabels,
  statusTypes,
} from '@/ingest/format'

const props = defineProps<{ job: Job; busy?: boolean }>()
const emit = defineEmits<{ action: [action: JobAction, job: Job] }>()

const visibleStages: StageName[] = ['fetch', 'assemble', 'extract', 'prepare', 'import', 'index']

const title = computed(() => {
  const crate = props.job.crates[0]
  if (crate) return props.job.crates.length > 1 ? `${crate.title} +${props.job.crates.length - 1}` : crate.title
  const part = props.job.parts[0]
  if (part?.filename) return props.job.parts.length > 1 ? `${part.filename} +${props.job.parts.length - 1}` : part.filename
  return props.job.source === 'url' ? 'Download from links' : 'Uploaded files'
})

const fetchLabel = computed(() => (props.job.source === 'upload' ? 'Upload' : stageLabels.fetch))

const currentStage = computed(() => {
  if (props.job.stage === 'done') return null
  const stage = props.job.stages[props.job.stage]
  return stage ? { name: props.job.stage, ...stage } : null
})

const showParts = computed(() => props.job.source === 'url' && props.job.stages.fetch.status !== 'done')

const totalSize = computed(() => props.job.parts.reduce((sum, part) => sum + (part.size ?? 0), 0))

const canPause = computed(() => props.job.status === 'running' || props.job.status === 'queued')
const canResume = computed(() => props.job.status === 'paused' || props.job.status === 'failed')
const canReindex = computed(
  () =>
    (props.job.status === 'completed' || props.job.status === 'failed') &&
    props.job.crates.some((crate) => crate.import) &&
    props.job.stages.import.status === 'done',
)
const canRemove = computed(() => props.job.status !== 'deleting')

function stageLabel(name: StageName): string {
  return name === 'fetch' ? fetchLabel.value : stageLabels[name]
}

const progress = computed(() => {
  const stage = currentStage.value
  if (!stage || stage.status !== 'running') return null
  const label = stageLabel(stage.name)
  if (stage.name === 'index') {
    return { label, value: percent(stage.done, stage.total), text: `${stage.done} of ${stage.total ?? '?'} members visible` }
  }
  if (stage.name === 'prepare') {
    return { label, value: percent(stage.done, stage.total), text: `${stage.done} of ${stage.total ?? '?'} crates` }
  }
  if (stage.total) {
    return { label, value: percent(stage.done, stage.total), text: `${humanBytes(stage.done)} of ${humanBytes(stage.total)}` }
  }
  return { label, value: 0, text: stage.done ? humanBytes(stage.done) : 'working…', indeterminate: true }
})

function partPercent(received: number, size: number | null): number {
  return percent(received, size)
}

function crateVersion(crate: Job['crates'][number]): string {
  if (!crate.import) return crate.existing ? `updates ${crate.existing.head}` : 'new collection'
  if (!crate.import.changed) return `unchanged (${crate.import.version})`
  return crate.import.created ? `${crate.import.version} created` : `${crate.import.version} created (update)`
}

function indexText(crate: Job['crates'][number]): string {
  if (!crate.index) return props.job.options.index ? 'waiting' : 'not requested'
  const counts = `${crate.index.members} of ${crate.index.expected} members`
  if (crate.index.status === 'indexed') return `indexed · ${counts}`
  if (crate.index.status === 'settled') return `indexed · ${counts} (settled)`
  if (crate.index.status === 'timeout') return `still indexing · ${counts}`
  const checked = crate.index.parents ? ` · ${crate.index.verified ?? 0}/${crate.index.parents} objects verified` : ''
  return `indexing · ${counts}${checked}`
}
</script>

<template>
  <article class="job" :class="`job--${job.status}`">
    <header class="job__head">
      <div class="job__title">
        <h3>{{ title }}</h3>
        <p class="job__meta">
          {{ formatTime(job.createdAt) }} ·
          {{ job.source === 'url' ? `${job.parts.length} link${job.parts.length === 1 ? '' : 's'}` : `${job.parts.length} upload${job.parts.length === 1 ? '' : 's'}` }}
          <template v-if="totalSize"> · {{ humanBytes(totalSize) }}</template>
          · {{ job.options.payload === 'text' ? 'text only' : 'all files' }}
          <template v-if="job.queuePosition"> · position {{ job.queuePosition }} in queue</template>
        </p>
      </div>
      <el-tag :type="statusTypes[job.status]" effect="light" round>{{ statusLabels[job.status] }}</el-tag>
    </header>

    <ol class="stages" aria-label="Ingestion steps">
      <li
        v-for="name in visibleStages"
        :key="name"
        class="stage"
        :class="[`stage--${job.stages[name].status}`, { 'stage--current': job.stage === name }]"
      >
        <span class="stage__dot" aria-hidden="true" />
        <span class="stage__label">{{ stageLabel(name) }}</span>
      </li>
    </ol>

    <div v-if="progress" class="job__progress">
      <el-progress
        :percentage="'indeterminate' in progress ? 100 : progress.value"
        :indeterminate="'indeterminate' in progress"
        :duration="2"
        :stroke-width="10"
        :show-text="false"
      />
      <span class="job__progress-text">{{ progress.label }} · {{ progress.text }}</span>
    </div>
    <p v-if="currentStage?.message || job.stages.assemble.message" class="job__note">
      {{ currentStage?.message || job.stages.assemble.message }}
    </p>

    <el-alert v-if="job.error" type="error" :title="job.error" :closable="false" show-icon class="job__alert" />

    <ul v-if="showParts" class="parts">
      <li v-for="part in job.parts" :key="part.index" class="part">
        <div class="part__row">
          <span class="part__name" :title="part.url || ''">{{ part.filename || `Part ${part.index + 1}` }}</span>
          <span class="part__size">
            {{ humanBytes(part.received) }} / {{ humanBytes(part.size) }}
            <template v-if="part.speed && part.status === 'downloading'"> · {{ humanBytes(part.speed) }}/s</template>
          </span>
        </div>
        <el-progress
          :percentage="part.status === 'done' ? 100 : partPercent(part.received, part.size)"
          :stroke-width="6"
          :show-text="false"
          :status="part.status === 'done' ? 'success' : part.status === 'retrying' ? 'warning' : undefined"
        />
        <span v-if="part.message" class="part__message">{{ part.message }}</span>
      </li>
    </ul>

    <ul v-if="job.crates.length" class="crates">
      <li v-for="crate in job.crates" :key="crate.rootId" class="crate">
        <code class="crate__id">{{ crate.rootId }}</code>
        <div class="crate__facts">
          <span>{{ crate.counts.objects }} objects</span>
          <span>{{ crate.filesPresent }}/{{ crate.filesLocal }} files stored</span>
          <span>OCFL: {{ crateVersion(crate) }}</span>
          <span>Portal: {{ indexText(crate) }}</span>
        </div>
        <details v-if="crate.warnings.length || crate.changes.length" class="crate__details">
          <summary>{{ crate.changes.length }} normalisation step(s), {{ crate.warnings.length }} warning(s)</summary>
          <ul>
            <li v-for="change in crate.changes" :key="change">{{ change }}</li>
            <li v-for="warning in crate.warnings" :key="warning" class="is-warning">{{ warning }}</li>
          </ul>
        </details>
      </li>
    </ul>

    <footer class="job__foot">
      <details class="job__log">
        <summary>Activity log ({{ job.log.length }})</summary>
        <ol>
          <li v-for="(entry, index) in [...job.log].reverse()" :key="index" :class="`log--${entry.level}`">
            <time>{{ clock(entry.t) }}</time>
            <span>{{ entry.msg }}</span>
          </li>
        </ol>
      </details>
      <div class="job__actions">
        <el-button v-if="canPause" size="small" :disabled="busy" @click="emit('action', 'pause', job)">Pause</el-button>
        <el-button v-if="canResume" size="small" type="primary" :disabled="busy" @click="emit('action', 'resume', job)">
          Resume
        </el-button>
        <el-button v-if="canReindex" size="small" :disabled="busy" @click="emit('action', 'reindex', job)">Re-index</el-button>
        <el-button v-if="canRemove" size="small" type="danger" plain :disabled="busy" @click="emit('action', 'remove', job)">
          Remove
        </el-button>
      </div>
    </footer>
  </article>
</template>

<style scoped>
.job {
  border: 1px solid var(--rapid-border);
  border-radius: var(--rapid-radius-sm);
  padding: 16px 18px;
  background: var(--rapid-surface);
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.job--running {
  border-color: var(--rapid-brand-200);
  box-shadow: 0 0 0 3px var(--rapid-brand-50);
}

.job--failed {
  border-color: #f3c2c2;
}

.job__head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.job__title {
  min-width: 0;
}

.job__title h3 {
  margin: 0;
  font-size: 1.05rem;
  line-height: 1.3;
  overflow-wrap: anywhere;
}

.job__meta {
  margin: 4px 0 0;
  color: var(--rapid-muted);
  font-size: 0.85rem;
}

.stages {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.stage {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 0.8rem;
  background: #f3f1f2;
  color: var(--rapid-muted);
}

.stage__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.6;
}

.stage--done {
  background: #e8f5ec;
  color: #1f7a3d;
}

.stage--running {
  background: var(--rapid-brand-50);
  color: var(--rapid-brand);
  font-weight: 600;
}

.stage--running .stage__dot {
  opacity: 1;
  animation: pulse 1.2s ease-in-out infinite;
}

.stage--failed {
  background: #fdecec;
  color: #b42318;
}

.stage--paused {
  background: #fff4e5;
  color: #9a5b00;
}

.stage--skipped {
  text-decoration: line-through;
}

@keyframes pulse {
  0%,
  100% {
    transform: scale(1);
  }
  50% {
    transform: scale(1.6);
  }
}

.job__progress {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.job__progress-text,
.job__note {
  font-size: 0.85rem;
  color: var(--rapid-muted);
  margin: 0;
}

.job__alert {
  margin: 0;
}

.parts,
.crates {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.part__row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 0.85rem;
  margin-bottom: 4px;
}

.part__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.part__size {
  color: var(--rapid-muted);
  white-space: nowrap;
}

.part__message {
  display: block;
  font-size: 0.8rem;
  color: #9a5b00;
  margin-top: 2px;
}

.crate {
  padding: 10px 12px;
  border-radius: 10px;
  background: #faf8f9;
  border: 1px solid var(--rapid-border);
}

.crate__id {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.85rem;
  overflow-wrap: anywhere;
}

.crate__facts {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin-top: 6px;
  font-size: 0.85rem;
  color: var(--rapid-muted);
}

.crate__details,
.job__log {
  font-size: 0.85rem;
}

.crate__details summary,
.job__log summary {
  cursor: pointer;
  color: var(--rapid-brand);
  margin-top: 6px;
}

.crate__details ul {
  margin: 6px 0 0;
  padding-left: 18px;
}

.is-warning,
.log--warn {
  color: #9a5b00;
}

.log--error {
  color: #b42318;
}

.job__foot {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  flex-wrap: wrap;
}

.job__log {
  flex: 1 1 260px;
  min-width: 0;
}

.job__log ol {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  max-height: 220px;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.job__log li {
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: 8px;
}

.job__log time {
  color: var(--rapid-muted);
  font-variant-numeric: tabular-nums;
}

.job__actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
</style>
