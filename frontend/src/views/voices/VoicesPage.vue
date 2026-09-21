<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import PageFrame from '../../components/shared/PageFrame.vue'
import { useOrchestratorStore } from '../../stores/orchestrator'
import * as voicesApi from '../../api/voices'
import { listTracks, type SavedTrack } from '../../api/tracks'
import ModelOfflineBanner from '../../components/shared/ModelOfflineBanner.vue'
import type { Voice, ConversionTask } from '../../api/voices'

const orchestrator = useOrchestratorStore()
const { t } = useI18n()

const modelStatus = computed(() => orchestrator.statuses.yue2?.status ?? 'stopped')
const modelError = computed(() => orchestrator.statuses.yue2?.error ?? null)
const isRunning = computed(() => modelStatus.value === 'running')

const voices = ref<Voice[]>([])
const selected = ref<string>('')
const listError = ref('')

// --- import ---------------------------------------------------------------
const importName = ref('')
const importTranscript = ref('')
const importFile = ref<File | null>(null)
const importTrackId = ref<number | null>(null)
const importUseVocalStem = ref(true)
const importing = ref(false)
const importError = ref('')

// --- speak ----------------------------------------------------------------
const speakText = ref('')
const speakLanguage = ref('en')
const speaking = ref(false)
const speakError = ref('')
const speakUrl = ref('')

// --- convert --------------------------------------------------------------
const tracks = ref<SavedTrack[]>([])
const sourceTrackId = ref<number | null>(null)
const useVocalStem = ref(true)
const convertTask = ref<ConversionTask>('svc')
const convertFile = ref<File | null>(null)
const job = ref<voicesApi.ConversionJob | null>(null)
const jobId = ref('')
const convertError = ref('')
let pollTimer: number | undefined

const selectedTrack = computed(() => tracks.value.find((tr) => tr.id === sourceTrackId.value) || null)
const selectedTrackHasVocals = computed(() => !!selectedTrack.value?.stems?.vocals)
const converting = computed(() => job.value?.status === 'queued' || job.value?.status === 'running')

async function refreshVoices() {
  listError.value = ''
  try {
    voices.value = await voicesApi.listVoices()
    if (!voices.value.some((v) => v.name === selected.value)) {
      selected.value = voices.value[0]?.name ?? ''
    }
  } catch (err) {
    listError.value = err instanceof Error ? err.message : String(err)
  }
}

async function refreshTracks() {
  try {
    tracks.value = await listTracks()
  } catch {
    tracks.value = []
  }
}

function onImportFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0] ?? null
  importFile.value = file
  if (file && !importName.value) {
    importName.value = file.name.replace(/\.[^.]+$/, '')
  }
}

const importTrack = computed(() => tracks.value.find((tr) => tr.id === importTrackId.value) || null)
const importTrackHasVocals = computed(() => !!importTrack.value?.stems?.vocals)
const canImport = computed(
  () => !!importName.value.trim() && !importing.value && (importTrackId.value != null || !!importFile.value),
)

function onImportTrackChange() {
  // A track and a file are alternatives, not a pair - picking one clears the
  // other so the request is never ambiguous about where the clip came from.
  if (importTrackId.value != null) importFile.value = null
}

async function onImport() {
  if (!canImport.value) return
  importing.value = true
  importError.value = ''
  try {
    const source =
      importTrackId.value != null
        ? {
            trackId: importTrackId.value,
            stem: importUseVocalStem.value && importTrackHasVocals.value ? 'vocals' : undefined,
          }
        : { file: importFile.value! }
    const voice = await voicesApi.importVoice(importName.value.trim(), source, importTranscript.value)
    await refreshVoices()
    selected.value = voice.name
    importName.value = ''
    importTranscript.value = ''
    importFile.value = null
    importTrackId.value = null
  } catch (err) {
    importError.value = err instanceof Error ? err.message : String(err)
  } finally {
    importing.value = false
  }
}

async function onDelete(name: string) {
  await voicesApi.deleteVoice(name)
  await refreshVoices()
}

async function onSpeak() {
  if (!selected.value || !speakText.value.trim()) return
  speaking.value = true
  speakError.value = ''
  try {
    const blob = await voicesApi.speak(selected.value, speakText.value.trim(), speakLanguage.value)
    if (speakUrl.value) URL.revokeObjectURL(speakUrl.value)
    speakUrl.value = URL.createObjectURL(blob)
  } catch (err) {
    speakError.value = err instanceof Error ? err.message : String(err)
  } finally {
    speaking.value = false
  }
}

function onConvertFile(e: Event) {
  convertFile.value = (e.target as HTMLInputElement).files?.[0] ?? null
  if (convertFile.value) sourceTrackId.value = null
}

async function onConvert() {
  if (!selected.value) return
  convertError.value = ''
  try {
    const started = await voicesApi.convert(selected.value, {
      task: convertTask.value,
      title: selectedTrack.value ? `${selectedTrack.value.title} · ${selected.value}` : '',
      trackId: sourceTrackId.value ?? undefined,
      stem: sourceTrackId.value != null && useVocalStem.value && selectedTrackHasVocals.value ? 'vocals' : undefined,
      file: sourceTrackId.value == null ? convertFile.value ?? undefined : undefined,
    })
    jobId.value = started.job_id || ''
    job.value = started
    pollJob()
  } catch (err) {
    convertError.value = err instanceof Error ? err.message : String(err)
  }
}

function pollJob() {
  window.clearTimeout(pollTimer)
  if (!jobId.value) return
  pollTimer = window.setTimeout(async () => {
    try {
      job.value = await voicesApi.conversionStatus(jobId.value)
    } catch {
      // transient; the next tick retries
    }
    if (converting.value) pollJob()
    else await refreshTracks()
  }, 2000)
}

async function onCancelConvert() {
  if (!jobId.value) return
  job.value = await voicesApi.cancelConversion(jobId.value)
}

const resultTrack = computed(() =>
  job.value?.status === 'done' && job.value.track_id != null
    ? tracks.value.find((tr) => tr.id === job.value?.track_id) ?? null
    : null,
)

onMounted(() => {
  refreshVoices()
  refreshTracks()
})

onBeforeUnmount(() => {
  window.clearTimeout(pollTimer)
  if (speakUrl.value) URL.revokeObjectURL(speakUrl.value)
})
</script>

<template>
  <PageFrame :title="t('voices.title')" :subtitle="t('voices.subtitle')" accent="#a78bfa">


    <ModelOfflineBanner v-if="!isRunning" model-id="yue2" :status="modelStatus" :error="modelError" />

    <div v-else class="grid gap-6 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
      <section class="rounded-xl border border-border bg-panel p-4">
        <h2 class="text-sm font-semibold text-text">{{ t('voices.library') }}</h2>

        <p v-if="listError" class="mt-3 rounded-lg bg-status-failed/10 p-3 text-xs text-status-failed">{{ listError }}</p>
        <p v-else-if="!voices.length" class="mt-3 text-sm text-text-dim">{{ t('voices.empty') }}</p>

        <ul v-else class="mt-3 space-y-2">
          <li
            v-for="voice in voices"
            :key="voice.name"
            class="rounded-lg border p-3 transition-colors"
            :class="voice.name === selected ? 'border-accent1/60 bg-panel-2' : 'border-border'"
          >
            <div class="flex items-center gap-2">
              <button type="button" class="flex-1 text-left text-sm text-text" @click="selected = voice.name">
                {{ voice.name }}
              </button>
              <button type="button" class="text-xs text-text-dim hover:text-status-failed" @click="onDelete(voice.name)">
                {{ t('common.delete') }}
              </button>
            </div>
            <audio :src="voice.audio_url" controls class="mt-2 h-8 w-full"></audio>
            <p v-if="voice.transcript" class="mt-2 text-xs text-text-dim">{{ voice.transcript }}</p>
          </li>
        </ul>

        <div class="mt-5 border-t border-border pt-4">
          <h3 class="text-sm font-semibold text-text">{{ t('voices.importTitle') }}</h3>
          <p class="mt-1 text-xs text-text-dim">{{ t('voices.importHint') }}</p>
          <select
            v-model.number="importTrackId"
            class="mt-3 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text"
            @change="onImportTrackChange"
          >
            <option :value="null">{{ t('voices.importFromFile') }}</option>
            <option v-for="tr in tracks" :key="tr.id" :value="tr.id">{{ tr.title || `#${tr.id}` }}</option>
          </select>
          <input
            v-if="importTrackId == null"
            type="file"
            accept="audio/*"
            class="mt-2 w-full text-xs text-text-dim"
            @change="onImportFile"
          />
          <label v-else-if="importTrackHasVocals" class="mt-2 flex items-center gap-2 text-xs text-text-dim">
            <input v-model="importUseVocalStem" type="checkbox" />
            {{ t('voices.importUseVocalStem') }}
          </label>
          <p v-else class="mt-2 text-xs text-text-dim">{{ t('voices.importNoVocalStem') }}</p>
          <input
            v-model="importName"
            type="text"
            :placeholder="t('voices.namePlaceholder')"
            class="mt-2 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text"
          />
          <input
            v-model="importTranscript"
            type="text"
            :placeholder="t('voices.transcriptPlaceholder')"
            class="mt-2 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text"
          />
          <button
            type="button"
            class="accent-gradient mt-3 w-full rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            :disabled="!canImport"
            @click="onImport"
          >
            {{ importing ? t('voices.importing') : t('voices.import') }}
          </button>
          <p v-if="importError" class="mt-2 whitespace-pre-line rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">{{ importError }}</p>
        </div>
      </section>

      <div class="space-y-6">
        <section class="rounded-xl border border-border bg-panel p-4">
          <h2 class="text-sm font-semibold text-text">{{ t('voices.speakTitle') }}</h2>
          <p class="mt-1 text-xs text-text-dim">{{ t('voices.speakHint') }}</p>
          <textarea
            v-model="speakText"
            rows="3"
            :placeholder="t('voices.speakPlaceholder')"
            class="mt-3 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text"
          ></textarea>
          <div class="mt-3 flex flex-wrap items-center gap-3">
            <select v-model="speakLanguage" class="rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text">
              <option value="en">English</option>
              <option value="de">Deutsch</option>
              <option value="es">Español</option>
              <option value="fr">Français</option>
              <option value="it">Italiano</option>
              <option value="pl">Polski</option>
              <option value="pt">Português</option>
              <option value="nl">Nederlands</option>
              <option value="tr">Türkçe</option>
              <option value="ko">한국어</option>
              <option value="hi">हिन्दी</option>
              <option value="ar">العربية</option>
            </select>
            <button
              type="button"
              class="accent-gradient rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              :disabled="!selected || !speakText.trim() || speaking"
              @click="onSpeak"
            >
              {{ speaking ? t('voices.speaking') : t('voices.speak') }}
            </button>
            <span v-if="selected" class="text-xs text-text-dim">{{ t('voices.usingVoice', { voice: selected }) }}</span>
          </div>
          <audio v-if="speakUrl" :src="speakUrl" controls class="mt-3 w-full"></audio>
          <p v-if="speakError" class="mt-2 whitespace-pre-line rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">{{ speakError }}</p>
        </section>

        <section class="rounded-xl border border-border bg-panel p-4">
          <h2 class="text-sm font-semibold text-text">{{ t('voices.convertTitle') }}</h2>
          <p class="mt-1 text-xs text-text-dim">{{ t('voices.convertHint') }}</p>

          <div class="mt-3 grid gap-3 sm:grid-cols-2">
            <label class="text-xs text-text-dim">
              {{ t('voices.sourceTrack') }}
              <select
                v-model.number="sourceTrackId"
                class="mt-1 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text"
              >
                <option :value="null">{{ t('voices.sourceFromFile') }}</option>
                <option v-for="tr in tracks" :key="tr.id" :value="tr.id">{{ tr.title || `#${tr.id}` }}</option>
              </select>
            </label>
            <label class="text-xs text-text-dim">
              {{ t('voices.material') }}
              <select v-model="convertTask" class="mt-1 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text">
                <option value="svc">{{ t('voices.materialSinging') }}</option>
                <option value="vc">{{ t('voices.materialSpeech') }}</option>
              </select>
            </label>
          </div>

          <input
            v-if="sourceTrackId == null"
            type="file"
            accept="audio/*"
            class="mt-3 w-full text-xs text-text-dim"
            @change="onConvertFile"
          />
          <label v-else-if="selectedTrackHasVocals" class="mt-3 flex items-center gap-2 text-xs text-text-dim">
            <input v-model="useVocalStem" type="checkbox" />
            {{ t('voices.useVocalStem') }}
          </label>
          <p v-else class="mt-3 text-xs text-text-dim">{{ t('voices.noVocalStem') }}</p>

          <div class="mt-3 flex flex-wrap items-center gap-3">
            <button
              type="button"
              class="accent-gradient rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              :disabled="!selected || converting || (sourceTrackId == null && !convertFile)"
              @click="onConvert"
            >
              {{ converting ? t('voices.converting') : t('voices.convert') }}
            </button>
            <button v-if="converting" type="button" class="rounded-lg border border-border px-4 py-2 text-sm text-text-dim" @click="onCancelConvert">
              {{ t('common.cancel') }}
            </button>
            <span v-if="converting" class="text-xs text-text-dim">{{ t('voices.convertingHint') }}</span>
          </div>

          <p v-if="convertError" class="mt-2 whitespace-pre-line rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">{{ convertError }}</p>
          <p v-else-if="job?.status === 'failed'" class="mt-2 whitespace-pre-line rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">{{ job.error }}</p>

          <div v-if="resultTrack" class="mt-3 rounded-lg border border-border bg-panel-2 p-3">
            <p class="text-sm text-text">{{ resultTrack.title }}</p>
            <audio :src="resultTrack.audio_url" controls class="mt-2 w-full"></audio>
          </div>
        </section>
      </div>
    </div>
  </PageFrame>
</template>
