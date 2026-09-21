<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import * as mixApi from '../../api/mix'
import * as projectsApi from '../../api/projects'
import { getSharedAudioCtx } from '../../composables/audioPlayback'
import {
  STEM_NAMES,
  applyMixSettings,
  buildMixGraph,
  decodeStem,
  defaultChannelSettings,
  defaultMasterSettings,
  defaultMixSettings,
  disconnectMixGraph,
  getReverbImpulse,
  playFrom,
  getChannelLevel,
} from '../../audio/mixerEngine'
import type { ChannelSettings, MixGraph, MixSettings, PlaybackHandle, StemName } from '../../audio/mixerEngine'
import type { Clip, TimelineLane, TimelineProject } from '../../audio/timelineTypes'
import type { ModelId } from '../../types'
import ChannelStrip from './ChannelStrip.vue'
import PlayIcon from './icons/PlayIcon.vue'
import PauseIcon from './icons/PauseIcon.vue'

const router = useRouter()
const { t } = useI18n()

const props = defineProps<{
  trackId: number
  stemUrls: Record<string, string>
  title: string
  lyrics: string
  model: ModelId
}>()

const emit = defineEmits<{ close: [] }>()

const STEM_LABELS = computed<Record<StemName, string>>(() => ({
  vocals: t('library.stems.vocals'),
  drums: t('library.stems.drums'),
  bass: t('library.stems.bass'),
  other: t('library.stems.other'),
}))

const loading = ref(true)
const loadError = ref<string | null>(null)
const settings = ref<MixSettings>(defaultMixSettings())
const playing = ref(false)
const currentTime = ref(0)
const totalDuration = ref(0)
const stemLevels = ref<Record<StemName, { peak: number; clipping: boolean }>>(
  Object.fromEntries(STEM_NAMES.map((name) => [name, { peak: 0, clipping: false }])) as Record<StemName, { peak: number; clipping: boolean }>,
)
const masterLevel = ref({ peak: 0, clipping: false })
const openingEditor = ref(false)
const openEditorError = ref<string | null>(null)

let graph: MixGraph | null = null
let buffers: Record<StemName, AudioBuffer> | null = null
let playback: PlaybackHandle | null = null
let playStartCtxTime = 0
let playStartOffset = 0
let rafId: number | null = null
let settingsSaved = false

const stemSettings = Object.fromEntries(
  STEM_NAMES.map((name) => [
    name,
    computed<ChannelSettings>({
      get: () => settings.value.stems[name],
      set: (v) => {
        settings.value = { ...settings.value, stems: { ...settings.value.stems, [name]: v } }
      },
    }),
  ]),
) as Record<StemName, ReturnType<typeof computed<ChannelSettings>>>

const masterAsChannel = computed<ChannelSettings>({
  get: () => ({ ...settings.value.master, pan: 0, muted: false, solo: false }),
  set: (v) => {
    const { pan: _pan, muted: _muted, solo: _solo, ...rest } = v
    settings.value = { ...settings.value, master: rest }
  },
})

function reapply(): void {
  if (graph) applyMixSettings(graph, settings.value)
}

function tick(): void {
  if (graph) {
    const ctx = graph.ctx as AudioContext
    currentTime.value = Math.min(totalDuration.value, ctx.currentTime - playStartCtxTime + playStartOffset)
    for (const name of STEM_NAMES) {
      stemLevels.value[name] = getChannelLevel(graph.stems[name])
    }
    masterLevel.value = getChannelLevel(graph.master)
  }
  rafId = requestAnimationFrame(tick)
}
function startTicking(): void {
  stopTicking()
  rafId = requestAnimationFrame(tick)
}
function stopTicking(): void {
  if (rafId != null) cancelAnimationFrame(rafId)
  rafId = null
}

async function load(): Promise<void> {
  try {
    const [{ settings: saved }, decodedEntries] = await Promise.all([
      mixApi.getMixSettings(props.trackId),
      Promise.all(STEM_NAMES.map(async (name) => [name, await decodeStem(props.stemUrls[name])] as const)),
    ])
    settings.value = saved ?? defaultMixSettings()
    buffers = Object.fromEntries(decodedEntries) as Record<StemName, AudioBuffer>
    totalDuration.value = Math.max(...STEM_NAMES.map((name) => buffers![name].duration))
    const ctx = getSharedAudioCtx()
    graph = buildMixGraph(ctx, getReverbImpulse(ctx.sampleRate))
    reapply()
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

let seekToken = 0

// Owns starting playback from a given offset, always (re)creating the source
// nodes - used by both play() and seek(). A monotonic token guards against
// overlapping calls: ctx.resume() is async, so a rapid drag on the seek bar
// can fire several of these before the first one resolves; without the
// token, an earlier call resolving after a later one would resurrect stale
// sources and playback would appear to silently stop or ignore the seek.
async function playFromOffset(offset: number): Promise<void> {
  if (!graph || !buffers) return
  const token = ++seekToken
  await (graph.ctx as AudioContext).resume()
  if (token !== seekToken || !graph || !buffers) return // superseded, or the modal closed meanwhile
  playback?.stop()
  playStartOffset = offset
  playStartCtxTime = (graph.ctx as AudioContext).currentTime
  playback = playFrom(graph, buffers, offset, () => {
    playing.value = false
    currentTime.value = totalDuration.value
    stopTicking()
  })
  playing.value = true
  startTicking()
}

async function play(): Promise<void> {
  if (playing.value) return
  const offset = currentTime.value >= totalDuration.value ? 0 : currentTime.value
  await playFromOffset(offset)
}

function pause(): void {
  seekToken++ // invalidate any in-flight playFromOffset() from a pending seek
  playback?.stop()
  playback = null
  playing.value = false
  stopTicking()
  // Reset meters
  for (const name of STEM_NAMES) stemLevels.value[name] = { peak: 0, clipping: false }
  masterLevel.value = { peak: 0, clipping: false }
}

function seek(value: number): void {
  currentTime.value = value
  if (playing.value) void playFromOffset(value)
}

function resetChannel(name: StemName): void {
  settings.value = { ...settings.value, stems: { ...settings.value.stems, [name]: defaultChannelSettings() } }
}
function resetMaster(): void {
  settings.value = { ...settings.value, master: defaultMasterSettings() }
}
function resetAll(): void {
  settings.value = defaultMixSettings()
}

function teardownGraph(): void {
  seekToken++ // invalidate any in-flight playFromOffset()
  stopTicking()
  playback?.stop()
  playback = null
  if (graph) disconnectMixGraph(graph)
  graph = null
  for (const name of STEM_NAMES) stemLevels.value[name] = { peak: 0, clipping: false }
  masterLevel.value = { peak: 0, clipping: false }
}

async function doClose(): Promise<void> {
  pause()
  if (!settingsSaved) {
    settingsSaved = true
    try {
      await mixApi.saveMixSettings(props.trackId, settings.value)
    } catch {
      // best-effort - closing the mixer shouldn't be blocked by a save failure
    }
  }
  teardownGraph()
  emit('close')
}

// Hands the 4 stems off to the full timeline editor (one lane per stem,
// carrying over whatever volume/EQ/pan/etc. was already dialed in here)
// instead of rendering a flat mix from this modal - the timeline editor is
// a strict superset of what this fixed 4-channel mixer can do.
async function openInEditor(): Promise<void> {
  if (!buffers) return
  openingEditor.value = true
  openEditorError.value = null
  try {
    const lanes: TimelineLane[] = STEM_NAMES.filter((name) => props.stemUrls[name]).map((name) => {
      const clip: Clip = {
        id: crypto.randomUUID(),
        sourceUrl: props.stemUrls[name],
        sourceLabel: `${props.title} · ${STEM_LABELS.value[name]}`,
        timelineStart: 0,
        trimStart: 0,
        trimEnd: buffers![name].duration,
      }
      return { id: crypto.randomUUID(), name: STEM_LABELS.value[name], settings: { ...settings.value.stems[name] }, clips: [clip] }
    })
    const project: TimelineProject = { version: 1, lanes, master: { ...settings.value.master }, pxPerSecond: 40 }
    const created = await projectsApi.createProject(props.title, project)
    await doClose()
    await router.push(`/editor/${created.id}`)
  } catch (e) {
    openEditorError.value = e instanceof Error ? e.message : String(e)
  } finally {
    openingEditor.value = false
  }
}

function formatTime(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return '0:00'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape') void doClose()
}

watch(settings, reapply, { deep: true })

onMounted(() => {
  void load()
  window.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  teardownGraph()
})
</script>

<template>
  <Teleport to="body">
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" @mousedown.self="doClose">
      <div class="flex max-h-[90vh] w-full max-w-5xl flex-col gap-4 overflow-y-auto rounded-xl bg-panel p-4">
        <div class="flex items-center justify-between">
          <p class="truncate text-sm font-medium text-text">{{ t('mixer.title', { title }) }}</p>
          <button type="button" class="text-text-dim hover:text-status-failed" :title="t('common.close')" @click="doClose">✕</button>
        </div>

        <p v-if="loading" class="text-xs text-text-dim">{{ t('mixer.loadingTracks') }}</p>
        <p v-else-if="loadError" class="rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">{{ loadError }}</p>

        <template v-else>
          <div class="flex items-center gap-3 rounded-lg border border-border bg-panel-2 p-2">
            <button
              type="button"
              class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full accent-gradient text-white"
              @click="playing ? pause() : play()"
            >
              <PlayIcon v-if="!playing" class="w-[13px] h-[13px]" />
              <PauseIcon v-else class="w-[13px] h-[13px]" />
            </button>
            <input
              type="range"
              min="0"
              :max="totalDuration || 0.01"
              step="0.01"
              :value="currentTime"
              class="w-full accent-current"
              @input="seek(Number(($event.target as HTMLInputElement).value))"
            />
            <span class="w-20 shrink-0 text-right text-xs tabular-nums text-text-dim">{{ formatTime(currentTime) }} / {{ formatTime(totalDuration) }}</span>
          </div>

          <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <ChannelStrip
              v-for="name in STEM_NAMES"
              :key="name"
              :model-value="stemSettings[name].value"
              :label="STEM_LABELS[name]"
              show-pan-mute-solo
              :level="stemLevels[name]?.peak ?? 0"
              :clipping="stemLevels[name]?.clipping ?? false"
              @update:model-value="(v) => (stemSettings[name].value = v)"
              @reset="resetChannel(name)"
            />
            <ChannelStrip
              :model-value="masterAsChannel"
              :label="t('editor.master')"
              :level="masterLevel.peak"
              :clipping="masterLevel.clipping"
              @update:model-value="(v) => (masterAsChannel = v)"
              @reset="resetMaster"
            />
          </div>

          <div class="flex flex-wrap items-center justify-between gap-3 border-t border-border/60 pt-3">
            <button type="button" class="text-xs text-text-dim hover:underline" @click="resetAll">{{ t('mixer.resetAll') }}</button>
            <button
              type="button"
              class="accent-gradient rounded-lg px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
              :disabled="openingEditor"
              @click="openInEditor"
            >
              {{ openingEditor ? t('mixer.opening') : t('mixer.openInEditor') }}
            </button>
          </div>
          <p v-if="openEditorError" class="rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">{{ openEditorError }}</p>
        </template>
      </div>
    </div>
  </Teleport>
</template>
