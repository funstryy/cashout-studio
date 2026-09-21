<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import PageFrame from '../../components/shared/PageFrame.vue'
import * as separationApi from '../../api/separation'
import { listTracks, uploadTrack, type SavedTrack } from '../../api/tracks'
import ProgressBar from '../../components/shared/ProgressBar.vue'
import FileBrowser from '../../components/editor/FileBrowser.vue'
import { apiJson } from '../../api/http'
import type {
  EnsembleAlgorithm,
  OutputFormat,
  SeparationJob,
  SeparationModel,
  StemFilter,
} from '../../api/separation'

const { t } = useI18n()

const models = ref<SeparationModel[]>([])
const tracks = ref<SavedTrack[]>([])
const selectedModels = ref<string[]>([])
const trackId = ref<number | null>(null)

const ensembleMode = ref(false)
const algorithm = ref<EnsembleAlgorithm>('max_spec')
const outputFormat = ref<OutputFormat>('wav')
const stemFilter = ref<StemFilter>('all')
const sampleMode = ref(false)
const useGpu = ref(true)

const job = ref<SeparationJob | null>(null)
const jobId = ref('')
const error = ref('')
const importing = ref(false)
const dragging = ref(false)
let pollTimer: number | undefined

// Spelled out rather than built from the algorithm id: the ids are snake_case
// and the message keys are camelCase, so interpolating one into the other
// silently renders the key itself.
const ALGORITHM_HINT_KEYS: Record<EnsembleAlgorithm, string> = {
  max_spec: 'separation.maxSpecHint',
  min_spec: 'separation.minSpecHint',
  average: 'separation.averageHint',
}

const algorithmHint = computed(() => ALGORITHM_HINT_KEYS[algorithm.value])
const running = computed(() => job.value?.status === 'queued' || job.value?.status === 'running')
const installedModels = computed(() => models.value.filter((m) => m.installed))
const canStart = computed(() => trackId.value != null && selectedModels.value.length > 0 && !running.value)
const resultStems = computed(() => Object.entries(job.value?.stems ?? {}))

function onToggleModel(id: string) {
  if (ensembleMode.value) {
    const at = selectedModels.value.indexOf(id)
    if (at >= 0) selectedModels.value.splice(at, 1)
    else selectedModels.value.push(id)
  } else {
    selectedModels.value = [id]
  }
}

function onModeChange() {
  // Leaving ensemble mode with several models picked would silently run only
  // the first, so collapse the selection to match what the mode can do.
  if (!ensembleMode.value && selectedModels.value.length > 1) {
    selectedModels.value = selectedModels.value.slice(0, 1)
  }
}

async function refresh() {
  try {
    ;[models.value, tracks.value] = await Promise.all([separationApi.listModels(), listTracks()])
    if (!selectedModels.value.length && installedModels.value.length) {
      selectedModels.value = [installedModels.value[0].id]
    }
    if (trackId.value == null && tracks.value.length) {
      trackId.value = tracks.value[0].id
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

const browsing = ref(false)

/**
 * Import by path instead of by file dialog.
 *
 * The box below uses `<input type="file">`, which needs the host WebView to
 * put up a native picker. When it does not, the click does nothing at all
 * and there is nothing to report - which is what "I can't import a track"
 * looked like. Browsing server-side does not depend on the host at any
 * point, and it reaches the folders already configured in the file browser,
 * including the ones on D:.
 */
async function importFromDisk(path: string) {
  importing.value = true
  error.value = ''
  try {
    const track = await apiJson<{ id: number }>('/api/browser/import', { path })
    await refresh()
    trackId.value = track.id
    browsing.value = false
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    importing.value = false
  }
}

async function importFile(file: File | null | undefined) {
  if (!file) return
  importing.value = true
  error.value = ''
  try {
    // Uploading registers it as a track, which is what the rest of the app
    // already understands - the stems land on it, and the mixer and editor
    // pick them up like any other separation.
    const track = await uploadTrack(file, file.name.replace(/\.[^.]+$/, ''))
    await refresh()
    trackId.value = track.id
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    importing.value = false
  }
}

function onImportChange(e: Event) {
  const input = e.target as HTMLInputElement
  importFile(input.files?.[0])
  input.value = ''
}

function onDrop(e: DragEvent) {
  dragging.value = false
  importFile(e.dataTransfer?.files?.[0])
}

async function onStart() {
  if (trackId.value == null) return
  error.value = ''
  try {
    const started = await separationApi.startSeparation({
      track_id: trackId.value,
      models: selectedModels.value,
      algorithm: algorithm.value,
      output_format: outputFormat.value,
      stem_filter: stemFilter.value,
      sample_mode: sampleMode.value,
      use_gpu: useGpu.value,
    })
    jobId.value = started.job_id || ''
    job.value = started
    poll()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

function poll() {
  window.clearTimeout(pollTimer)
  if (!jobId.value) return
  pollTimer = window.setTimeout(async () => {
    try {
      job.value = await separationApi.separationStatus(jobId.value)
    } catch {
      // transient; the next tick retries
    }
    if (running.value) poll()
    else await refresh()
  }, 1500)
}

async function onStop() {
  if (!jobId.value) return
  job.value = await separationApi.cancelSeparation(jobId.value)
}

onMounted(refresh)
onBeforeUnmount(() => window.clearTimeout(pollTimer))
</script>

<template>
  <PageFrame :title="t('separation.title')" :subtitle="t('separation.subtitle')" accent="#34d399">


    <div class="grid gap-6 lg:grid-cols-3">
      <section class="rounded-xl border border-border bg-panel p-4">
        <h2 class="text-xs font-semibold uppercase tracking-wide text-accent1">{{ t('separation.method') }}</h2>
        <select
          v-model="ensembleMode"
          class="mt-2 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text"
          @change="onModeChange"
        >
          <option :value="false">{{ t('separation.singleModel') }}</option>
          <option :value="true">{{ t('separation.ensembleMode') }}</option>
        </select>

        <h2 class="mt-4 text-xs font-semibold uppercase tracking-wide text-accent1">{{ t('separation.source') }}</h2>
        <select
          v-model.number="trackId"
          class="mt-2 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text"
        >
          <option v-for="tr in tracks" :key="tr.id" :value="tr.id">{{ tr.title || `#${tr.id}` }}</option>
        </select>

        <label
          class="mt-2 flex cursor-pointer flex-col items-center gap-1 rounded-lg border border-dashed px-3 py-4 text-center text-xs transition-colors"
          :class="dragging ? 'border-accent1 bg-accent1/10 text-text' : 'border-border text-text-dim hover:text-text'"
          @dragover.prevent="dragging = true"
          @dragleave.prevent="dragging = false"
          @drop.prevent="onDrop"
        >
          <input type="file" accept="audio/*" class="hidden" @change="onImportChange" />
          <span>{{ importing ? t('separation.importing') : t('separation.importTrack') }}</span>
          <span class="text-[11px]">{{ t('separation.importHint') }}</span>
        </label>
        <button
          type="button"
          class="mt-1.5 w-full rounded-lg border border-border px-3 py-1.5 text-xs text-text-dim transition-colors hover:text-text"
          @click="browsing = !browsing"
        >{{ browsing ? t('separation.browseClose') : t('separation.browseDisk') }}</button>

        <div v-if="browsing" class="mt-2 h-64 overflow-hidden rounded-lg border border-border">
          <FileBrowser
            always-show-pick
            :pick-label="t('separation.importThis')"
            @pick="(payload) => importFromDisk(payload.path)"
          />
        </div>

        <p v-if="!tracks.length" class="mt-2 text-xs text-text-dim">{{ t('separation.noTracks') }}</p>

        <h2 v-if="ensembleMode" class="mt-4 text-xs font-semibold uppercase tracking-wide text-accent1">
          {{ t('separation.algorithm') }}
        </h2>
        <select
          v-if="ensembleMode"
          v-model="algorithm"
          class="mt-2 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text"
        >
          <option value="max_spec">{{ t('separation.maxSpec') }}</option>
          <option value="min_spec">{{ t('separation.minSpec') }}</option>
          <option value="average">{{ t('separation.average') }}</option>
        </select>
        <p v-if="ensembleMode" class="mt-2 text-xs text-text-dim">{{ t(algorithmHint) }}</p>
      </section>

      <section class="rounded-xl border border-border bg-panel p-4">
        <h2 class="text-xs font-semibold uppercase tracking-wide text-accent1">{{ t('separation.availableModels') }}</h2>
        <ul class="mt-2 space-y-1 rounded-lg border border-border bg-panel-2 p-2">
          <li v-for="model in models" :key="model.id">
            <button
              type="button"
              class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm transition-colors disabled:opacity-40"
              :class="selectedModels.includes(model.id) ? 'bg-accent1/20 text-text' : 'text-text-dim hover:text-text'"
              :disabled="!model.installed"
              @click="onToggleModel(model.id)"
            >
              <span class="h-2 w-2 shrink-0 rounded-full" :class="selectedModels.includes(model.id) ? 'bg-accent1' : 'bg-border'"></span>
              <span class="flex-1">{{ model.label }}</span>
              <span class="text-[10px] uppercase text-text-dim">{{ model.stems.length }} {{ t('separation.stemsUnit') }}</span>
            </button>
            <p v-if="!model.installed" class="px-2 text-[11px] text-status-failed">{{ t('separation.notInstalled') }}</p>
          </li>
        </ul>
        <p class="mt-2 text-xs text-text-dim">
          {{ ensembleMode ? t('separation.pickSeveral') : t('separation.pickOne') }}
        </p>
      </section>

      <section class="rounded-xl border border-border bg-panel p-4">
        <h2 class="text-xs font-semibold uppercase tracking-wide text-accent1">{{ t('separation.options') }}</h2>
        <label class="mt-2 flex items-center gap-2 text-sm text-text-dim">
          <input v-model="useGpu" type="checkbox" />
          {{ t('separation.gpuConversion') }}
        </label>
        <label class="mt-1 flex items-center gap-2 text-sm text-text-dim">
          <input v-model="sampleMode" type="checkbox" />
          {{ t('separation.sampleMode') }}
        </label>

        <h2 class="mt-4 text-xs font-semibold uppercase tracking-wide text-accent1">{{ t('separation.stems') }}</h2>
        <select
          v-model="stemFilter"
          class="mt-2 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text"
        >
          <option value="all">{{ t('separation.allStems') }}</option>
          <option value="vocals">{{ t('separation.vocalsOnly') }}</option>
          <option value="instrumental">{{ t('separation.instrumentalOnly') }}</option>
        </select>

        <h2 class="mt-4 text-xs font-semibold uppercase tracking-wide text-accent1">{{ t('separation.format') }}</h2>
        <div class="mt-2 flex gap-4 text-sm text-text-dim">
          <label v-for="fmt in (['wav', 'flac', 'mp3'] as const)" :key="fmt" class="flex items-center gap-1.5">
            <input v-model="outputFormat" type="radio" :value="fmt" />
            {{ fmt.toUpperCase() }}
          </label>
        </div>
      </section>
    </div>

    <div class="mt-6 flex flex-wrap items-center gap-3">
      <button
        type="button"
        class="accent-gradient rounded-lg px-6 py-3 text-sm font-medium text-white disabled:opacity-50"
        :disabled="!canStart"
        @click="onStart"
      >
        {{ t('separation.start') }}
      </button>
      <button
        v-if="running"
        type="button"
        class="rounded-lg border border-border px-4 py-3 text-sm text-text-dim"
        @click="onStop"
      >
        {{ t('separation.stop') }}
      </button>
      <span v-if="sampleMode" class="text-xs text-text-dim">{{ t('separation.sampleModeHint') }}</span>
    </div>

    <div v-if="running" class="mt-4">
      <ProgressBar :value="Math.round((job?.progress ?? 0) * 100)" />
      <p class="mt-1 text-xs text-text-dim">{{ job?.step }}</p>
    </div>

    <p v-if="error" class="mt-4 whitespace-pre-line rounded-lg bg-status-failed/10 p-3 text-xs text-status-failed">{{ error }}</p>
    <p v-else-if="job?.status === 'failed'" class="mt-4 whitespace-pre-line rounded-lg bg-status-failed/10 p-3 text-xs text-status-failed">{{ job.error }}</p>

    <section v-if="resultStems.length && !running" class="mt-6 rounded-xl border border-border bg-panel p-4">
      <h2 class="text-sm font-semibold text-text">{{ t('separation.results') }}</h2>
      <p v-if="sampleMode" class="mt-1 text-xs text-text-dim">{{ t('separation.samplePreviewNote') }}</p>
      <div class="mt-3 grid gap-3 sm:grid-cols-2">
        <div v-for="[name, url] in resultStems" :key="name" class="rounded-lg border border-border bg-panel-2 p-3">
          <div class="flex items-center justify-between">
            <span class="text-sm text-text">{{ name }}</span>
            <a :href="url" :download="`${name}.${outputFormat}`" class="text-xs text-accent1">{{ t('common.download') }}</a>
          </div>
          <audio :src="url" controls class="mt-2 w-full"></audio>
        </div>
      </div>
    </section>
  </PageFrame>
</template>
