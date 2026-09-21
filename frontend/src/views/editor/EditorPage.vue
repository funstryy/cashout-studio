<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useEditorStore } from '../../stores/editor'
import { useCollabStore } from '../../stores/collab'
import { useTimelineEngine } from '../../composables/useTimelineEngine'
import { getSharedAudioCtx } from '../../composables/audioPlayback'
import { decodeStem } from '../../audio/mixerEngine'
import type { ChannelSettings } from '../../audio/mixerEngine'
import type { Clip } from '../../audio/timelineTypes'
import { DEFAULT_BPM, projectBpm, projectLoop } from '../../audio/timelineTypes'
import { startMetronome, type MetronomeHandle } from '../../audio/metronome'
import { useVoiceRecorder } from '../../composables/useVoiceRecorder'
import { encodeWav } from '../../audio/wavEncoder'
import { encodeMp3 } from '../../audio/mp3Encoder'
import { useOrchestratorStore } from '../../stores/orchestrator'
import { useDawShortcuts, type DawTool } from '../../composables/useDawShortcuts'
import { useNativeTransport } from '../../composables/useNativeTransport'
import * as tracksApi from '../../api/tracks'
import TimelineLane from '../../components/editor/TimelineLane.vue'
import LibraryPicker from '../../components/editor/LibraryPicker.vue'
import PluginRack from '../../components/editor/PluginRack.vue'
import ChannelRackPanel from '../../components/editor/ChannelRack.vue'
import GenerativeFill from '../../components/editor/GenerativeFill.vue'
import FileBrowser from '../../components/editor/FileBrowser.vue'
import MixerPanel from '../../components/editor/MixerPanel.vue'
import PanelFrame from '../../components/editor/PanelFrame.vue'
import EffectDesigner from '../../components/editor/EffectDesigner.vue'
import TransportBar from '../../components/editor/TransportBar.vue'
import MenuBar from '../../components/editor/MenuBar.vue'
import CollabPanel from '../../components/editor/CollabPanel.vue'
import AudioEnginePanel from '../../components/editor/AudioEnginePanel.vue'
import MasteringPanel from '../../components/editor/MasteringPanel.vue'
import CoProducerPanel from '../../components/editor/CoProducerPanel.vue'
import { defaultRack, newRackChannel, rackTotalSteps, renderRack } from '../../audio/channelRack'
import * as pluginsApi from '../../api/plugins'
import * as voicesApi from '../../api/voices'

const props = defineProps<{ id: string }>()

const store = useEditorStore()
const collab = useCollabStore()
const engine = useTimelineEngine()
const native = useNativeTransport()
const router = useRouter()
const route = useRoute()
const { t } = useI18n()

const buffers = ref<Map<string, AudioBuffer>>(new Map())
const loadingAudio = ref(true)
const pickerOpenForNewLane = ref(false)
const exportFormat = ref<'wav' | 'mp3'>('wav')
const exporting = ref(false)
const exportError = ref<string | null>(null)
const exportedOk = ref(false)

const laneLevels = ref<{ peak: number; clipping: boolean }[]>([])
const masterLevel = ref<{ peak: number; clipping: boolean }>({ peak: 0, clipping: false })

let rafId: number | null = null
let playStartCtxTime = 0
let playStartOffset = 0

const recorder = useVoiceRecorder()
const metronomeOn = ref(false)
const recordLaneIndex = ref(0)
const recordError = ref('')
let metronome: MetronomeHandle | null = null
const recordStartSecRef = ref(0)

const bpm = computed({
  get: () => projectBpm(store.project),
  set: (value: number) => {
    store.project.bpm = Math.min(300, Math.max(20, Math.round(value) || DEFAULT_BPM))
  },
})
const loop = computed(() => projectLoop(store.project))

function setLoop(patch: Partial<{ enabled: boolean; startSec: number; endSec: number }>): void {
  store.project.loop = { ...projectLoop(store.project), ...patch }
}

function startMetronomeIfOn(fromSec: number, atCtxTime: number): void {
  metronome?.stop()
  metronome = null
  if (!metronomeOn.value) return
  const ctx = getSharedAudioCtx()
  metronome = startMetronome(ctx, ctx.destination, bpm.value, fromSec, atCtxTime)
}

function stopTicking(): void {
  if (rafId != null) cancelAnimationFrame(rafId)
  rafId = null
  laneLevels.value = store.project.lanes.map(() => ({ peak: 0, clipping: false }))
  masterLevel.value = { peak: 0, clipping: false }
}
function tick(): void {
  const ctx = getSharedAudioCtx()
  const position = ctx.currentTime - playStartCtxTime + playStartOffset
  const region = loop.value
  // Looping re-seeks rather than scheduling ahead: clips are scheduled at
  // absolute times when playback starts, so a loop point is a new start.
  if (region.enabled && region.endSec > region.startSec && position >= region.endSec) {
    seek(region.startSec)
    return
  }
  store.playheadSec = Math.min(store.totalDuration, position)
  laneLevels.value = store.project.lanes.map((_, i) => engine.getLaneLevel(i))
  masterLevel.value = engine.getMasterLevel()
  rafId = requestAnimationFrame(tick)
}
function startTicking(): void {
  stopTicking()
  rafId = requestAnimationFrame(tick)
}

function onEnded(): void {
  // A loop keeps running past the end of the material, so the tail of an
  // empty loop region must not count as the project finishing.
  if (loop.value.enabled && loop.value.endSec > loop.value.startSec) return
  store.playing = false
  store.playheadSec = store.totalDuration
  metronome?.stop()
  metronome = null
  stopTicking()
}

/**
 * Whether the native engine is holding an output open.
 *
 * Checked at the moment of pressing play rather than kept in sync: the
 * engine can be opened or closed from its own panel at any time, and a
 * cached answer here would send a transport command to a device that is no
 * longer there.
 */
async function nativeOwnsPlayback(): Promise<boolean> {
  return native.refresh()
}

async function play(): Promise<void> {
  if (store.playing) return
  const from = store.playheadSec >= store.totalDuration ? 0 : store.playheadSec

  if (await nativeOwnsPlayback()) {
    store.playing = true
    // No metronome on this path yet: the click is scheduled into the Web
    // Audio graph, and running it there against a playhead driven by the
    // engine would drift. Better absent than wrong.
    await native.play(
      store.project,
      from,
      (position) => {
        store.playheadSec = Math.min(store.totalDuration, position)
      },
      onEnded,
      store.totalDuration,
    )
    return
  }

  playStartOffset = from
  playStartCtxTime = getSharedAudioCtx().currentTime
  store.playing = true
  await engine.play(store.project, buffers.value, from, onEnded)
  startMetronomeIfOn(from, playStartCtxTime)
  startTicking()
}

function pause(): void {
  engine.stop()
  void native.stop()
  metronome?.stop()
  metronome = null
  store.playing = false
  stopTicking()
}

function stopTransport(): void {
  pause()
  store.playheadSec = loop.value.enabled ? loop.value.startSec : 0
}

function seek(value: number): void {
  store.playheadSec = value
  if (native.available.value) {
    // A seek while stopped still moves the engine's playhead, so pressing
    // play afterwards starts from where the cursor is rather than from
    // wherever the last stop left it.
    void native.seek(value)
    return
  }
  if (store.playing) {
    playStartOffset = value
    playStartCtxTime = getSharedAudioCtx().currentTime
    void engine.play(store.project, buffers.value, value, onEnded)
    startMetronomeIfOn(value, playStartCtxTime)
  }
}

async function toggleRecord(): Promise<void> {
  recordError.value = ''
  if (recorder.recording.value) {
    const take = await recorder.stop()
    pause()
    if (!take) return
    try {
      await addRecordedTake(take.blob, take.durationSec)
    } catch (err) {
      recordError.value = err instanceof Error ? err.message : String(err)
    }
    return
  }

  if (!recorder.armed.value) {
    await recorder.arm()
    if (!recorder.armed.value) {
      recordError.value = recorder.error.value || 'microphone unavailable'
      return
    }
  }
  recordStartSecRef.value = store.playheadSec
  recorder.start()
  // Rolling while recording is what makes overdubbing possible - you play
  // along to what's already there.
  if (!store.playing) await play()
}

/** Store the take as a library track, then drop it on the armed lane where
 * recording began - so it behaves like every other clip afterwards. */
async function addRecordedTake(blob: Blob, durationSec: number): Promise<void> {
  const name = `Take ${new Date().toLocaleTimeString()}`
  const file = new File([blob], `${name}.wav`, { type: 'audio/wav' })
  const uploaded = await tracksApi.uploadTrack(file, name)

  const lane = store.project.lanes[recordLaneIndex.value]
  if (!lane) return
  buffers.value.set(uploaded.audio_url, await decodeStem(uploaded.audio_url))
  lane.clips.push({
    id: `clip-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    sourceUrl: uploaded.audio_url,
    sourceLabel: name,
    timelineStart: recordStartSecRef.value,
    trimStart: 0,
    trimEnd: durationSec,
  })
  // Snapshot so a take can be undone like any other timeline edit.
  store.commitSnapshot()
}


// --- plugin inserts --------------------------------------------------------
// Printed, not live: a VST is native code and the timeline plays in the
// browser, so applying an insert renders the clip through the host process
// and swaps in the result.
const pluginLaneIndex = ref<number | null>(null)
const pluginBusy = ref(false)
const pluginError = ref('')
/** Set when a track's plugin chip was clicked, so the rack opens on it. */
const pluginPreselect = ref<string | null>(null)

// ────── Channel rack ──────
//
// The rack lives on the project, so it saves, loads and undoes with
// everything else. It is created on demand rather than in the project
// template, so an arrangement that never uses one carries nothing extra.
const rackBouncing = ref(false)
const rackChannelForPlugins = ref<string | null>(null)

// Read-only: the project gets its rack in load(), because a computed that
// writes to the store runs during render and would fight the reactivity it
// is part of. The fallback only covers the frame before a project has loaded.
const rack = computed(() => store.project.rack ?? defaultRack())

/**
 * Every project gets a rack, whatever it arrived without.
 *
 * `rack` above falls back to a throwaway when the project has none, and
 * anything pushed into that throwaway is silently lost - a step click that
 * does nothing. load() used to be the only way a project appeared, and it
 * installs one; a project adopted from a collaborator does not go through
 * load(), so the guarantee has to live with the project instead.
 */
watch(
  () => store.project,
  (project) => {
    if (!project.rack) project.rack = defaultRack()
  },
  { immediate: true, deep: false },
)

const rackPluginChannel = computed(() =>
  rack.value.channels.find((c) => c.id === rackChannelForPlugins.value) ?? null,
)

async function addRackChannel(payload: { sourceUrl: string; sourceLabel: string }): Promise<void> {
  try {
    if (!buffers.value.has(payload.sourceUrl)) {
      buffers.value.set(payload.sourceUrl, await decodeStem(payload.sourceUrl))
    }
    rack.value.channels.push(
      newRackChannel(payload.sourceLabel, payload.sourceUrl, rackTotalSteps(rack.value)),
    )
    store.commitSnapshot()
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  }
}

/**
 * Renders the pattern and drops it on the timeline as a clip.
 *
 * The clip is trimmed to the pattern length so repeats butt up against each
 * other; the rendered tail past that point stays in the file, reachable by
 * dragging the clip's right edge, so a long 808 is not cut off.
 */
async function bounceRack(repeats: number): Promise<void> {
  rackBouncing.value = true
  try {
    const { buffer, patternSec } = await renderRack({
      rack: rack.value,
      master: store.project.master,
      buffers: buffers.value,
      bpm: projectBpm(store.project),
      repeats,
      sampleRate: getSharedAudioCtx().sampleRate,
    })
    const name = `${store.projectName} · ${t('rack.title')}`
    const file = new File([encodeWav(buffer)], `${name}.wav`, { type: 'audio/wav' })
    const uploaded = await tracksApi.uploadTrack(file, name)
    buffers.value.set(uploaded.audio_url, await decodeStem(uploaded.audio_url))

    const lane = store.addLane()
    store.renameLane(lane.id, t('rack.title'))
    store.addClip(lane.id, {
      id: crypto.randomUUID(),
      sourceUrl: uploaded.audio_url,
      sourceLabel: name,
      timelineStart: store.playheadSec,
      trimStart: 0,
      trimEnd: Math.min(patternSec, buffer.duration),
    })
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  } finally {
    rackBouncing.value = false
  }
}

/** Printing a plugin onto a rack channel replaces the channel's sample, the
 *  same bounce-in-place a track gets. */
async function applyPluginToChannel(payload: {
  pluginPath: string
  params: Record<string, number>
  stateKey: string
  label: string
}): Promise<void> {
  const channel = rackPluginChannel.value
  if (!channel) return
  const trackId = trackIdFromUrl(channel.sourceUrl)
  if (trackId == null) {
    pluginError.value = t('plugins.noClips')
    return
  }

  pluginBusy.value = true
  pluginError.value = ''
  try {
    const started = await pluginsApi.processWithPlugin({
      plugin_path: payload.pluginPath,
      track_id: trackId,
      title: `${channel.name} · ${payload.label}`,
      state_key: payload.stateKey,
      params: payload.params,
    })
    if (!started.job_id) throw new Error(started.error || t('plugins.failed'))

    let job = started
    while (job.status === 'queued' || job.status === 'running') {
      await new Promise((resolve) => setTimeout(resolve, 1500))
      job = await pluginsApi.pluginJobStatus(started.job_id)
    }
    if (job.status !== 'done' || job.track_id == null) {
      throw new Error(job.error || t('plugins.failed'))
    }

    const url = `/api/tracks/${job.track_id}/audio`
    buffers.value.set(url, await decodeStem(url))
    channel.sourceUrl = url
    channel.name = `${channel.name} · ${payload.label}`
    store.commitSnapshot()
    rackChannelForPlugins.value = null
  } catch (err) {
    pluginError.value = err instanceof Error ? err.message : String(err)
  } finally {
    pluginBusy.value = false
  }
}
// ──────────────────────────


const pluginLaneTitle = computed(() =>
  pluginLaneIndex.value == null ? '' : store.project.lanes[pluginLaneIndex.value]?.name ?? '',
)

/** Clips point at "/api/tracks/<id>/audio"; the job needs that id. */
function trackIdFromUrl(url: string): number | null {
  const match = url.match(/\/api\/tracks\/(\d+)\//)
  return match ? Number(match[1]) : null
}

async function applyPlugin(payload: {
  pluginPath: string
  params: Record<string, number>
  stateKey: string
  label: string
}) {
  const laneIndex = pluginLaneIndex.value
  if (laneIndex == null) return
  const lane = store.project.lanes[laneIndex]
  if (!lane?.clips.length) {
    pluginError.value = t('plugins.noClips')
    return
  }

  pluginBusy.value = true
  pluginError.value = ''
  try {
    for (const clip of lane.clips) {
      const trackId = trackIdFromUrl(clip.sourceUrl)
      if (trackId == null) continue
      const started = await pluginsApi.processWithPlugin({
        plugin_path: payload.pluginPath,
        track_id: trackId,
        title: `${lane.name} · ${payload.label}`,
        state_key: payload.stateKey,
        params: payload.params,
      })
      if (!started.job_id) continue

      let job = started
      while (job.status === 'queued' || job.status === 'running') {
        await new Promise((resolve) => setTimeout(resolve, 1500))
        job = await pluginsApi.pluginJobStatus(started.job_id)
      }
      if (job.status !== 'done' || job.track_id == null) {
        throw new Error(job.error || t('plugins.failed'))
      }

      // Point the clip at the printed take. Trim points survive because the
      // host returns exactly as many frames as it was given.
      const url = `/api/tracks/${job.track_id}/audio`
      buffers.value.set(url, await decodeStem(url))
      clip.sourceUrl = url
      clip.sourceLabel = `${clip.sourceLabel} · ${payload.label}`
    }

    // Recorded on the track once every clip is through, so a run that failed
    // half way does not leave the track claiming an effect it does not have.
    if (!lane.plugins) lane.plugins = []
    lane.plugins.push({
      name: payload.label,
      path: payload.pluginPath,
      stateKey: payload.stateKey,
      appliedAt: new Date().toISOString(),
    })
    store.commitSnapshot()
    pluginLaneIndex.value = null
  } catch (err) {
    pluginError.value = err instanceof Error ? err.message : String(err)
  } finally {
    pluginBusy.value = false
  }
}

// ────── Generative fill ──────
//
// The engine edits audio, so what comes back has to become a real track
// before the timeline can play it - same path a bounce takes.
// ────── Re-voicing ──────
//
// The other half of the vocal answer. Stable Audio 3 is instrumental-only and
// no model reliably invents a convincing rap flow, so the cadence comes from
// a take recorded on this timeline and Seed-VC swaps only the timbre. Its
// `svc` task is the singing one, which is what a rapped take needs - `vc` is
// tuned for speech and flattens the performance.
const revoiceLaneIndex = ref<number | null>(null)
const voiceList = ref<voicesApi.Voice[]>([])
const revoiceBusy = ref(false)
const revoiceError = ref('')

async function openRevoice(index: number) {
  revoiceLaneIndex.value = index
  revoiceError.value = ''
  try {
    voiceList.value = await voicesApi.listVoices()
  } catch (e) {
    revoiceError.value = e instanceof Error ? e.message : String(e)
  }
}

async function applyRevoice(voiceName: string) {
  const laneIndex = revoiceLaneIndex.value
  if (laneIndex == null) return
  const lane = store.project.lanes[laneIndex]
  if (!lane?.clips.length) {
    revoiceError.value = t('revoice.noClips')
    return
  }

  revoiceBusy.value = true
  revoiceError.value = ''
  try {
    for (const clip of lane.clips) {
      const trackId = trackIdFromUrl(clip.sourceUrl)
      if (trackId == null) continue
      const started = await voicesApi.convert(voiceName, {
        task: 'svc',
        trackId,
        title: `${lane.name} · ${voiceName}`,
      })
      if (!started.job_id) continue

      let job = started
      while (job.status === 'queued' || job.status === 'running') {
        await new Promise((resolve) => setTimeout(resolve, 2000))
        job = await voicesApi.conversionStatus(started.job_id)
      }
      if (job.status !== 'done' || job.track_id == null) {
        throw new Error(job.error || t('revoice.failed'))
      }

      const url = `/api/tracks/${job.track_id}/audio`
      buffers.value.set(url, await decodeStem(url))
      clip.sourceUrl = url
      clip.sourceLabel = `${clip.sourceLabel} · ${voiceName}`
    }
    store.commitSnapshot()
    revoiceLaneIndex.value = null
  } catch (e) {
    revoiceError.value = e instanceof Error ? e.message : String(e)
  } finally {
    revoiceBusy.value = false
  }
}
// ────────────────────────

// ────── File browser ──────
const browserOpen = ref(true)
/** Set by F5-F9: which panel the function keys just asked for. */
const openPanel = ref<'playlist' | 'rack' | 'pianoRoll' | 'mixer' | null>(null)

// ────── Mixer ──────
// null means the master strip is selected, which is also its default: the
// master is the one strip that is always there.
const mixerSelected = ref<number | null>(null)

/** One place for the transport's record button to go, so the bar does not
 *  have to know about arming, lanes or takes. */
function onTransportRecord() {
  void toggleRecord()
}

function onMixerLane(payload: { index: number; settings: ChannelSettings }) {
  const lane = store.project.lanes[payload.index]
  if (lane) store.updateLaneSettings(lane.id, payload.settings)
}
// ───────────────────

/**
 * A browsed file becomes a real library track before it reaches the
 * timeline. Pointing a clip straight at a path on disk would make the
 * project silently depend on a file the user can move or delete, and a
 * project that breaks when you tidy Downloads is not a project.
 */
async function onBrowserPick(payload: { path: string; name: string }) {
  try {
    const response = await fetch(`/api/browser/file?path=${encodeURIComponent(payload.path)}`)
    if (!response.ok) throw new Error(`could not read ${payload.name}`)
    const file = new File([await response.blob()], payload.name)
    const uploaded = await tracksApi.uploadTrack(file, payload.name)
    const buffer = await decodeStem(uploaded.audio_url)
    buffers.value.set(uploaded.audio_url, buffer)

    const lane = store.addLane()
    store.renameLane(lane.id, payload.name.replace(/\.[^.]+$/, ''))
    store.addClip(lane.id, {
      id: crypto.randomUUID(),
      sourceUrl: uploaded.audio_url,
      sourceLabel: payload.name,
      timelineStart: store.playheadSec,
      trimStart: 0,
      trimEnd: buffer.duration,
    })
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  }
}
// ──────────────────────────

const orchestratorStore = useOrchestratorStore()
const aceRunning = computed(() => orchestratorStore.statuses?.ace_step?.status === 'running')

async function bufferToTrack(buffer: AudioBuffer, title: string): Promise<string> {
  const file = new File([encodeWav(buffer)], `${title}.wav`, { type: 'audio/wav' })
  const uploaded = await tracksApi.uploadTrack(file, title)
  buffers.value.set(uploaded.audio_url, await decodeStem(uploaded.audio_url))
  return uploaded.audio_url
}

/** The regenerated lane replaces its clips with one clip spanning the whole
 *  lane: the splice already rebuilt the track as a single continuous take,
 *  and keeping the old clip boundaries would re-cut audio that no longer has
 *  them. */
async function onReplaceLane(payload: { laneIndex: number; buffer: AudioBuffer; label: string }) {
  const lane = store.project.lanes[payload.laneIndex]
  if (!lane) return
  try {
    const url = await bufferToTrack(payload.buffer, `${payload.label} · fill`)
    lane.clips = [{
      id: crypto.randomUUID(),
      sourceUrl: url,
      sourceLabel: `${payload.label} · fill`,
      timelineStart: 0,
      trimStart: 0,
      trimEnd: payload.buffer.duration,
    }]
    store.commitSnapshot()
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  }
}

async function onNewPart(payload: { buffer: AudioBuffer; name: string }) {
  try {
    const url = await bufferToTrack(payload.buffer, payload.name)
    const lane = store.addLane()
    store.renameLane(lane.id, payload.name)
    store.addClip(lane.id, {
      id: crypto.randomUUID(),
      sourceUrl: url,
      sourceLabel: payload.name,
      timelineStart: 0,
      trimStart: 0,
      trimEnd: payload.buffer.duration,
    })
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  }
}
// ─────────────────────────────

const timelineWidthPx = computed(() => Math.max(400, (store.totalDuration + 10) * store.project.pxPerSecond))

const rulerMarks = computed<number[]>(() => {
  const step = store.project.pxPerSecond < 20 ? 10 : store.project.pxPerSecond < 60 ? 5 : 1
  const maxT = timelineWidthPx.value / store.project.pxPerSecond
  const marks: number[] = []
  for (let t = 0; t <= maxT; t += step) marks.push(t)
  return marks
})

function onRulerClick(evt: MouseEvent): void {
  const rect = (evt.currentTarget as HTMLElement).getBoundingClientRect()
  const x = evt.clientX - rect.left
  seek(Math.max(0, x / store.project.pxPerSecond))
}

const snapCandidates = computed<number[]>(() => {
  const edges = [0]
  for (const lane of store.project.lanes) {
    for (const clip of lane.clips) {
      edges.push(clip.timelineStart, clip.timelineStart + (clip.trimEnd - clip.trimStart))
    }
  }
  return edges
})

function formatTime(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return '0:00'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function onAddLaneClick(): void {
  pickerOpenForNewLane.value = true
}

async function onPickForNewLane(payload: { sourceUrl: string; sourceLabel: string }): Promise<void> {
  pickerOpenForNewLane.value = false
  let buffer = buffers.value.get(payload.sourceUrl)
  if (!buffer) {
    buffer = await decodeStem(payload.sourceUrl)
    buffers.value.set(payload.sourceUrl, buffer)
  }
  const lane = store.addLane()
  store.renameLane(lane.id, payload.sourceLabel)
  const clip: Clip = {
    id: crypto.randomUUID(),
    sourceUrl: payload.sourceUrl,
    sourceLabel: payload.sourceLabel,
    timelineStart: 0,
    trimStart: 0,
    trimEnd: buffer.duration,
  }
  store.addClip(lane.id, clip)
}

async function doSave(): Promise<void> {
  const wasNew = store.projectId == null
  await store.save()
  if (wasNew && store.projectId != null) {
    await router.replace(`/editor/${store.projectId}`)
  }
}

async function doExport(): Promise<void> {
  exporting.value = true
  exportError.value = null
  exportedOk.value = false
  try {
    const rendered = await engine.render(store.project, buffers.value, store.totalDuration)
    const blob = exportFormat.value === 'wav' ? encodeWav(rendered) : encodeMp3(rendered)
    await tracksApi.saveTrack(
      { model: 'editor', title: store.projectName, lyrics: '', params: { project_export: true, project_id: store.projectId } },
      blob,
      exportFormat.value,
    )
    exportedOk.value = true
  } catch (e) {
    exportError.value = e instanceof Error ? e.message : String(e)
  } finally {
    exporting.value = false
  }
}

async function load(): Promise<void> {
  pause()
  engine.teardown()
  loadingAudio.value = true
  if (props.id === 'new') {
    store.newProject()
  } else {
    await store.loadProject(Number(props.id))
  }
  // Projects saved before the rack existed have none; give them an empty one
  // so the panel has something to write into.
  if (!store.project.rack) store.project.rack = defaultRack()
  buffers.value = await engine.decodeAll(store.project)
  engine.ensureGraph(store.project.lanes.length)
  engine.applySettings(store.project)
  loadingAudio.value = false
}

/**
 * Audio for clips that arrived over the network.
 *
 * load() decodes everything the project references at open time, which is
 * the whole story when you are working alone. In a session clips appear
 * without any local action - a friend drops an 808 in and this side has a
 * clip pointing at a file it has never fetched. Without this the clip draws
 * and plays silence.
 *
 * Keyed on the set of source URLs rather than the project, so dragging a
 * clip around does not re-enter it.
 */
watch(
  () => store.project.lanes.flatMap((lane) => lane.clips.map((clip) => clip.sourceUrl)).join('|'),
  async () => {
    if (!collab.live) return
    const wanted = new Set(store.project.lanes.flatMap((lane) => lane.clips.map((c) => c.sourceUrl)))
    let added = false
    for (const url of wanted) {
      if (buffers.value.has(url)) continue
      try {
        buffers.value.set(url, await decodeStem(url))
        added = true
      } catch {
        // The host may have gone, or it is refusing us. Leaving the clip
        // silent is better than tearing the arrangement down around it.
      }
    }
    // A new Map so the timeline's waveforms notice.
    if (added) buffers.value = new Map(buffers.value)
    engine.ensureGraph(store.project.lanes.length)
    engine.applySettings(store.project)
  },
)

async function onDropAudio(laneId: string, payload: { file: File; timelineStart: number }): Promise<void> {
  try {
    const uploaded = await tracksApi.uploadTrack(payload.file)
    let buffer = buffers.value.get(uploaded.audio_url)
    if (!buffer) {
      buffer = await decodeStem(uploaded.audio_url)
      buffers.value.set(uploaded.audio_url, buffer)
    }
    const clip: Clip = {
      id: crypto.randomUUID(),
      sourceUrl: uploaded.audio_url,
      sourceLabel: uploaded.title || payload.file.name,
      timelineStart: payload.timelineStart,
      trimStart: 0,
      trimEnd: buffer.duration,
    }
    store.addClip(laneId, clip)
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  }
}

watch(() => props.id, load, { immediate: true })
watch(
  () => store.project,
  async () => {
    engine.ensureGraph(store.project.lanes.length)
    engine.applySettings(store.project)
    for (const lane of store.project.lanes) {
      for (const clip of lane.clips) {
        if (!buffers.value.has(clip.sourceUrl)) {
          try {
            const buf = await decodeStem(clip.sourceUrl)
            buffers.value.set(clip.sourceUrl, buf)
          } catch {}
        }
      }
    }
  },
  { deep: true },
)

/** The pointer tool, FL's toolbar in a variable. */
const tool = ref<DawTool>('draw')

const clipboard = ref<Clip | null>(null)

const TOOLS: { id: DawTool; key: string; label: string }[] = [
  { id: 'draw', key: 'p', label: 'editor.toolDraw' },
  { id: 'select', key: 'e', label: 'editor.toolSelect' },
  { id: 'slice', key: 'c', label: 'editor.toolSlice' },
  { id: 'delete', key: 'd', label: 'editor.toolDelete' },
  { id: 'mute', key: 'm', label: 'editor.toolMute' },
]

function selectedClip(): Clip | null {
  if (!store.selectedClipId) return null
  return store.findClip(store.selectedClipId)?.clip ?? null
}

/** Whatever the playhead is currently over, if anything. */
function clipUnderPlayhead(): Clip | null {
  const at = store.playheadSec
  for (const lane of store.project.lanes) {
    for (const clip of lane.clips) {
      if (at >= clip.timelineStart && at < clip.timelineStart + (clip.trimEnd - clip.trimStart)) {
        return clip
      }
    }
  }
  return null
}

/**
 * What an edit key acts on.
 *
 * The selection first, then whatever the playhead is over. Measured the hard
 * way: with nothing selected, Ctrl+B did nothing at all and gave no reason -
 * the key appeared broken when it was simply being precise. Falling back to
 * the playhead is what every DAW does and what makes these usable without
 * clicking first.
 */
function editTarget(): Clip | null {
  return selectedClip() ?? clipUnderPlayhead()
}

/** Cut the target in two at the playhead. */
function splitAtPlayhead() {
  const at = store.playheadSec
  const target = editTarget()
  if (!target) return
  // A cut has to land inside the clip; the store refuses the edges anyway,
  // but preferring the clip actually under the playhead avoids trying to
  // split a selected clip the playhead is nowhere near.
  const inside = at > target.timelineStart &&
    at < target.timelineStart + (target.trimEnd - target.trimStart)
  const clip = inside ? target : clipUnderPlayhead()
  if (clip) store.splitClip(clip.id, at)
}

function onClipClick(clipId: string, atSec: number) {
  // Slice and delete act on click; every other tool selects.
  if (tool.value === 'slice') {
    store.splitClip(clipId, atSec)
    return
  }
  if (tool.value === 'delete') {
    store.removeClip(clipId)
    return
  }
  store.selectedClipId = clipId
}

useDawShortcuts({
  setTool: (value) => (tool.value = value),
  playPause: () => (store.playing ? pause() : void play()),
  stop: stopTransport,
  record: () => void toggleRecord(),
  toggleLoop: () =>
    setLoop({ enabled: !loop.value.enabled, endSec: loop.value.endSec || Math.max(4, store.totalDuration) }),
  save: () => void doSave(),
  undo: () => store.undo(),
  redo: () => store.redo(),
  duplicate: () => {
    const clip = editTarget()
    if (clip) store.duplicateClip(clip.id)
  },
  remove: () => {
    const clip = editTarget()
    if (clip) store.removeClip(clip.id)
  },
  copy: () => {
    const clip = editTarget()
    if (clip) clipboard.value = { ...clip }
  },
  paste: () => {
    const source = clipboard.value
    if (!source) return
    // Onto the selected clip's lane, or the first one, and at the playhead -
    // which is where a paste belongs in an arrangement.
    const lane = store.selectedClipId
      ? store.findClip(store.selectedClipId)?.lane
      : store.project.lanes[0]
    if (lane) store.pasteClip(source, lane.id, store.playheadSec)
  },
  splitAtPlayhead,
  togglePanel: showPanel,
})

/**
 * Where everybody else is looking.
 *
 * Sent on every playhead move: the store throttles it, and a collaborator's
 * position is the one thing that has to feel immediate - a ghost playhead
 * that lags by a second is worse than none at all.
 */
watch(
  () => [store.playheadSec, store.selectedClipId] as const,
  ([at, selected]) => {
    if (collab.live) collab.sendCursor(at, selected)
  },
)

watch(
  () => store.playing,
  (playing) => {
    if (collab.live) collab.sendTransport(playing, store.playheadSec)
  },
)

/** Peer playheads, minus your own - drawn over the ruler. */
const peerCursors = computed(() =>
  collab.others
    .map((peer) => ({
      id: peer.peer_id,
      name: peer.name,
      colour: peer.colour,
      left: (collab.cursors[peer.peer_id] ?? peer.playhead) * store.project.pxPerSecond,
    }))
    .filter((cursor) => Number.isFinite(cursor.left)),
)

function onTimelineWheel(e: WheelEvent) {
  if (e.ctrlKey || e.metaKey) {
    e.preventDefault()
    const delta = e.deltaY > 0 ? -5 : 5
    store.setZoom(store.project.pxPerSecond + delta)
  }
}

function onBeforeUnload(e: BeforeUnloadEvent) {
  if (store.dirty) {
    e.preventDefault()
    e.returnValue = ''
  }
}

const collabPanel = ref<{ reveal: () => void } | null>(null)
// The co-producer's "master it" note opens the mastering panel rather than
// mastering behind the user's back - it is a suggestion, not an autopilot.
const masteringPanel = ref<{ reveal: () => void } | null>(null)
const rackComponent = ref<{ openRoll: (id?: string) => boolean } | null>(null)

/**
 * The dock along the bottom, and the rail down the side.
 *
 * One window in the dock at a time. The strip of buttons at the top of the
 * workspace chooses which, so they behave exactly like FL's toolbar: press
 * the lit one again and the dock closes. Stacking the rack and the mixer
 * both below the song forever - which is what this page used to do - means
 * neither of them is ever fully on screen.
 */
type DockTab = 'rack' | 'mixer'
const dockTab = ref<DockTab | null>('mixer')
const toolsOpen = ref(true)

const DOCK_HEIGHT_KEY = 'cashout_dock_height'

/** Remembered, because the right dock height is a per-person preference and
 *  having to drag it back on every launch is what makes people stop using a
 *  resizable panel at all. */
const dockHeight = ref(readDockHeight())

function readDockHeight(): number {
  try {
    const saved = Number(localStorage.getItem(DOCK_HEIGHT_KEY))
    return Number.isFinite(saved) && saved >= 120 ? saved : 260
  } catch {
    return 260
  }
}

/**
 * Routes a menu pick to the thing the editor already does.
 *
 * A flat switch rather than handlers hung off each item, so the menu
 * definition stays a description of the program and this stays the one
 * place that says what each entry runs. An id with no case here is a menu
 * entry for a feature that does not exist, which is exactly the kind of
 * thing that should be obvious in review.
 */
function onMenu(id: string) {
  switch (id) {
    case 'file.new': return void router.push('/editor/new')
    case 'file.open': return void router.push('/editor')
    case 'file.save': return void doSave()
    case 'file.export': return void doExport()
    case 'file.close': return void router.push('/editor')

    case 'edit.undo': return store.undo()
    case 'edit.redo': return store.redo()
    case 'edit.split': return splitAtPlayhead()
    case 'edit.addTrack': return onAddLaneClick()

    case 'view.playlist': return showPanel('playlist')
    case 'view.rack': return showPanel('rack')
    case 'view.pianoRoll': return showPanel('pianoRoll')
    case 'view.browser': return showPanel('browser')
    case 'view.mixer': return showPanel('mixer')
    case 'view.tools': toolsOpen.value = !toolsOpen.value; return

    case 'transport.play': return void (store.playing ? pause() : play())
    case 'transport.stop': return stopTransport()
    case 'transport.record': return void toggleRecord()
    case 'transport.loop':
      setLoop({
        enabled: !loop.value.enabled,
        endSec: loop.value.endSec || Math.max(4, store.totalDuration),
      })
      return
    case 'transport.metronome': metronomeOn.value = !metronomeOn.value; return

    // The shortcuts are already printed beside the menu items and on every
    // toolbar button's tooltip, so this opens the one panel that is not
    // discoverable any other way rather than a dialogue repeating them.
    case 'help.shortcuts': toolsOpen.value = true; return
    case 'help.faq': return void router.push('/help')
    case 'help.about': return void router.push('/about')
  }
}

function startDockResize(event: PointerEvent) {
  const startY = event.clientY
  const startHeight = dockHeight.value
  const move = (e: PointerEvent) => {
    // Dragging up grows it, which is the direction the edge is being
    // pulled. Bounded so the dock can never eat the arrangement whole.
    dockHeight.value = Math.max(
      120,
      Math.min(window.innerHeight - 240, startHeight + (startY - e.clientY)),
    )
  }
  const done = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', done)
    try {
      localStorage.setItem(DOCK_HEIGHT_KEY, String(Math.round(dockHeight.value)))
    } catch {
      // Private mode. It simply will not be remembered.
    }
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', done)
}

// Same order and same keys as FL, because anybody coming from it already
// has these in their hands.
const WINDOWS = [
  { id: 'playlist', label: 'editor.playlist', key: 'F5' },
  { id: 'rack', label: 'rack.title', key: 'F6' },
  { id: 'pianoRoll', label: 'pianoRoll.title', key: 'F7' },
  { id: 'browser', label: 'browser.title', key: 'F8' },
  { id: 'mixer', label: 'dawMixer.title', key: 'F9' },
] as const

/**
 * The FL-style window buttons.
 *
 * `openPanel` used to be set by the F5-F9 shortcuts and read by nothing at
 * all - the keys did nothing and there was no way to reach the piano roll
 * except by finding a channel and clicking its own button. These are the
 * things the shortcuts were always meant to drive.
 */
/**
 * Lays a suggested progression into the rack as notes.
 *
 * Into a new channel rather than over whatever is already there: the
 * co-producer is allowed to offer something, not to overwrite the part you
 * wrote. Snapshotted first, so it is one undo away like any other edit.
 */
function writeProgression(payload: { label: string; bars: number[][] }) {
  const target = rack.value
  if (!store.project.rack) store.project.rack = defaultRack()
  store.snapshot()

  const stepsPerBar = target.stepsPerBar
  const channel = newRackChannel(payload.label, '', rackTotalSteps(target))
  channel.mode = 'notes'
  channel.notes = []

  payload.bars.forEach((chord, bar) => {
    for (const key of chord) {
      channel.notes!.push({
        id: crypto.randomUUID(),
        start: bar * stepsPerBar,
        // A bar each, so the progression reads as blocks rather than stabs.
        length: stepsPerBar,
        key,
        velocity: 0.8,
      })
    }
  })

  store.project.rack!.channels.push(channel)
  store.commitSnapshot()

  // Put it in front of them - a progression written into a panel nobody has
  // open has not been written anywhere useful.
  showPanel('pianoRoll')
  void nextTick(() => rackComponent.value?.openRoll(channel.id))
}

/**
 * Drives the workspace from the window strip.
 *
 * It used to scroll a panel into view and roll it open, because everything
 * lived in one long document. Nothing scrolls now, so the same buttons
 * switch what the dock is showing - and pressing the lit one closes it,
 * which is the behaviour anybody arriving from FL already expects.
 */
function showPanel(panel: 'playlist' | 'rack' | 'pianoRoll' | 'mixer' | 'browser') {
  if (panel === 'browser') {
    browserOpen.value = !browserOpen.value
    return
  }

  openPanel.value = panel

  if (panel === 'playlist') {
    // The arrangement is always on screen, so this uncovers it rather than
    // scrolling to it.
    dockTab.value = null
    return
  }

  if (panel === 'mixer' || panel === 'rack') {
    dockTab.value = dockTab.value === panel ? null : panel
    return
  }

  // The piano roll lives inside the rack, so the rack has to be in the dock
  // before it can put anything on screen - and after the dock has actually
  // rendered, or the roll opens inside a container that is not there yet.
  dockTab.value = 'rack'
  void nextTick(() => {
    if (!rackComponent.value?.openRoll()) {
      store.error = t('rack.noChannelsForRoll')
    }
  })
}

/** The name a session shows to everyone else, remembered between sessions. */
function collabName(): string {
  try {
    return localStorage.getItem('cashout_collab_name') || ''
  } catch {
    return ''
  }
}

function showCollabPanel() {
  // The collaboration panel lives in the tools rail, which can be closed.
  toolsOpen.value = true
  collabPanel.value?.reveal()
}

/**
 * Acts on the choice made back on the projects page.
 *
 * `?host=1` is "multiplayer project" and `?join=<code>` is a friend's
 * invite. Both only get as far as opening the panel when this machine has
 * no display name yet - starting a session anonymously would put "Guest" on
 * everyone's screen, and the name box is right there once the panel is
 * open.
 */
async function applySessionIntent() {
  const wantsHost = route.query.host === '1'
  const invite = typeof route.query.join === 'string' ? route.query.join : ''
  if (!wantsHost && !invite) return

  showCollabPanel()
  const name = collabName()
  if (!name) return

  if (invite) await collab.join(invite, name)
  else await collab.host(name)

  // Dropped from the URL once acted on, so a refresh or a back-navigation
  // does not try to start a second session on top of the live one.
  await router.replace({ path: route.path })
}

onMounted(() => {
  window.addEventListener('beforeunload', onBeforeUnload)
  void applySessionIntent()
})

onBeforeUnmount(() => {
  stopTicking()
  native.stopPolling()
  void native.stop()
  engine.teardown()
  window.removeEventListener('beforeunload', onBeforeUnload)
})

onBeforeRouteLeave((_to, _from, next) => {
  if (store.dirty) {
    const confirmLeave = window.confirm(t('editor.confirmLeave'))
    if (!confirmLeave) {
      next(false)
      return
    }
  }
  next()
})
</script>

<template>
  <!--
    A workspace, not a page.

    This was a column of panels stacked down a scrolling document:
    arrangement, then mixer, then six tool panels, and the transport
    scrolled away with them. No workstation is built that way, and it is
    the single thing that made the studio read as a web app however the
    panels themselves were painted. A transport you cannot reach
    mid-take is not a transport.

    So: the shell is exactly the height of the window and never scrolls.
    Chrome is pinned at the top, the arrangement takes every pixel that
    is left, the rack and the mixer share a resizable dock along the
    bottom, and the tools live in a rail beside the song rather than
    below it. Only the insides of those zones scroll.
  -->
  <div class="flex h-svh min-h-0 flex-col overflow-hidden bg-bg">

    <MenuBar
      :can-undo="store.canUndo"
      :can-redo="store.canRedo"
      :playing="store.playing"
      :loop-enabled="loop.enabled"
      :metronome="metronomeOn"
      :browser-open="browserOpen"
      :tools-open="toolsOpen"
      :dock-tab="dockTab"
      :dirty="store.dirty"
      @action="onMenu"
    />

    <header class="shrink-0 space-y-1 border-b border-black bg-panel/70 px-1.5 py-1.5">
      <!-- FL's window buttons. The shortcut is on the tooltip because that
           is how anybody ends up learning them. -->
      <div class="rack-strip flex flex-wrap items-center gap-1 border border-border px-1.5 py-1">
        <button
          v-for="item in WINDOWS"
          :key="item.id"
          type="button"
          class="key flex items-center gap-1.5 px-2.5 py-1 text-[11px]"
          :class="openPanel === item.id ? 'key-on' : ''"
          :title="`${t(item.label)} (${item.key})`"
          @click="showPanel(item.id)"
        >
          <!-- The lamp says which window you are in without the button having
               to change colour wholesale, the way a desk's bank-select row
               works. -->
          <span
            class="led h-[4px] w-[4px]"
            :style="{
              background: openPanel === item.id ? 'currentColor' : '#0c1219',
              color: openPanel === item.id ? 'currentColor' : 'transparent',
            }"
          ></span>
          <span class="text-[9px] text-text-faint">{{ item.key }}</span>{{ t(item.label) }}
        </button>
      </div>

      <TransportBar
        :playing="store.playing"
        :recording="recorder.recording.value"
        :armed="recorder.armed.value"
        :position-sec="store.playheadSec"
        :total-sec="store.totalDuration"
        :bpm="bpm"
        :loop-enabled="loop.enabled"
        :metronome="metronomeOn"
        :dirty="store.dirty"
        :saving="store.saving"
        :project-name="store.projectName"
        :can-undo="store.canUndo"
        :can-redo="store.canRedo"
        :collab-live="collab.live"
        :collab-peers="collab.peers.length"
        @play="play"
        @pause="pause"
        @stop="stopTransport"
        @record="onTransportRecord"
        @update:bpm="bpm = $event"
        @update:project-name="store.projectName = $event"
        @toggle-loop="setLoop({ enabled: !loop.enabled, endSec: loop.endSec || Math.max(4, store.totalDuration) })"
        @toggle-metronome="metronomeOn = !metronomeOn"
        @save="doSave"
        @undo="store.undo()"
        @redo="store.redo()"
        @collab="showCollabPanel"
      />

      <p v-if="store.loading || loadingAudio" class="text-xs text-text-dim">{{ t('common.loading') }}</p>
      <p v-if="recordError" class="rounded bg-status-failed/10 px-2 py-1 text-xs text-status-failed">{{ recordError }}</p>
    </header>

    <div class="flex min-h-0 flex-1">

      <!-- Browser. A fixed rail, so picking a sample does not scroll the
           song away underneath you. -->
      <aside
        v-if="browserOpen"
        class="hidden w-56 shrink-0 flex-col border-r border-black md:flex"
      >
        <FileBrowser @pick="onBrowserPick" />
      </aside>

      <main class="flex min-w-0 flex-1 flex-col overflow-hidden">
        <!-- The arrangement is the only thing here that grows. That is
             the whole point of the layout. -->
        <div class="min-h-0 flex-1 overflow-hidden p-1.5">
          <template v-if="!store.loading && !loadingAudio">
          <!-- Loop range and zoom live with the playlist they act on, not on the
               transport: they are per-view settings, not per-song ones. -->
          <PanelFrame class="h-full" :title="t('editor.playlist')" :badge="`${store.project.lanes.length} ${t('editor.tracksShort')}`" dense>
            <template #actions>
              <!-- FL's toolbar: one letter each, and the letter is the shortcut. -->
              <div class="mr-1 flex items-center gap-0.5 rounded bg-panel-2 p-0.5">
                <button
                  v-for="item in TOOLS"
                  :key="item.id"
                  type="button"
                  class="rounded px-1.5 py-0.5 text-[10px] font-semibold transition-colors"
                  :class="tool === item.id ? 'bg-accent1 text-[#04121a]' : 'text-text-faint hover:text-text'"
                  :title="`${t(item.label)} (${item.key.toUpperCase()})`"
                  @click="tool = item.id"
                >{{ item.key.toUpperCase() }}</button>
              </div>
              <button
                type="button"
                class="rounded bg-panel-2 px-2 py-0.5 text-[10px] text-text-dim hover:text-text"
                :title="t('editor.splitHint')"
                @click="splitAtPlayhead"
              >{{ t('editor.split') }}</button>
              <template v-if="loop.enabled">
                <input
                  type="number" min="0" step="0.5" :value="loop.startSec"
                  class="w-14 rounded bg-panel-2 px-1 py-0.5 text-[10px] tabular-nums text-text"
                  :title="t('editor.loopStart')"
                  @change="setLoop({ startSec: Number(($event.target as HTMLInputElement).value) })"
                />
                <input
                  type="number" min="0" step="0.5" :value="loop.endSec"
                  class="w-14 rounded bg-panel-2 px-1 py-0.5 text-[10px] tabular-nums text-text"
                  :title="t('editor.loopEnd')"
                  @change="setLoop({ endSec: Number(($event.target as HTMLInputElement).value) })"
                />
              </template>
              <input
                type="range" min="10" max="200"
                :value="store.project.pxPerSecond"
                class="w-20"
                :title="t('editor.zoom')"
                @input="store.setZoom(Number(($event.target as HTMLInputElement).value))"
              />
              <button
                type="button"
                class="rounded bg-panel-2 px-2 py-0.5 text-[10px] text-text-dim hover:text-text"
                @click="onAddLaneClick"
              >{{ t('editor.addTrack') }}</button>
            </template>

          <div class="inset overflow-x-auto" @wheel="onTimelineWheel">
            <div>
              <!-- The scale. A ruler with only labelled marks on it is a list of
                   times; a ruler you can actually measure against has minor
                   graduations between them, and those are one repeating
                   gradient rather than a per-second element. -->
              <div class="rack-strip flex">
                <div class="w-56 shrink-0 border-r border-black"></div>
                <div
                  class="relative h-6 flex-1 cursor-pointer"
                  :style="{
                    minWidth: timelineWidthPx + 'px',
                    backgroundImage: `repeating-linear-gradient(90deg,
                      rgba(255,255,255,0.13) 0 1px,
                      transparent 1px ${store.project.pxPerSecond}px)`,
                    backgroundSize: `auto 7px`,
                    backgroundRepeat: 'repeat-x',
                    backgroundPosition: 'left bottom',
                  }"
                  @click="onRulerClick"
                >
                  <div
                    v-for="mark in rulerMarks"
                    :key="mark"
                    class="engraved absolute top-0 bottom-0 border-l border-white/25 pl-1 text-[10px] tabular-nums text-text-dim"
                    :style="{ left: mark * store.project.pxPerSecond + 'px' }"
                  >
                    {{ formatTime(mark) }}
                  </div>
                  <div
                    v-for="cursor in peerCursors"
                    :key="cursor.id"
                    class="pointer-events-none absolute top-0 bottom-0 w-px"
                    :style="{ left: cursor.left + 'px', background: cursor.colour }"
                  >
                    <span
                      class="absolute -top-0.5 left-0.5 whitespace-nowrap rounded-sm px-1 text-[8px] font-medium text-bg"
                      :style="{ background: cursor.colour }"
                    >{{ cursor.name }}</span>
                  </div>
                  <div
                    class="absolute top-0 bottom-0 w-0.5 bg-status-failed shadow-[0_0_6px_rgba(239,68,68,0.6)]"
                    :style="{ left: store.playheadSec * store.project.pxPerSecond + 'px' }"
                  >
                    <div class="absolute -top-1 -left-1 h-0 w-0 border-x-[5px] border-t-[6px] border-x-transparent border-t-status-failed"></div>
                  </div>
                </div>
              </div>

              <TimelineLane
                v-for="(lane, idx) in store.project.lanes"
                :key="lane.id"
                :lane="lane"
                :px-per-second="store.project.pxPerSecond"
                :buffers="buffers"
                :recording="recorder.recording.value && idx === recordLaneIndex
                  ? { startSec: recordStartSecRef, nowSec: store.playheadSec }
                  : null"
                :snap-candidates="snapCandidates"
                :selected-clip-id="store.selectedClipId"
                :width-px="timelineWidthPx"
                :level="laneLevels[idx]"
                @update:settings="(v) => store.updateLaneSettings(lane.id, v)"
                @rename="(name) => store.renameLane(lane.id, name)"
                @move-clip="(p) => store.updateClip(p.clipId, { timelineStart: p.timelineStart })"
                @trim-clip="(p) => store.updateClip(p.clipId, { trimStart: p.trimStart, trimEnd: p.trimEnd, timelineStart: p.timelineStart })"
                @drag-end="store.commitSnapshot()"
                @drop-audio="(p) => onDropAudio(lane.id, p)"
                @select-clip="onClipClick"
                @remove-clip="(id) => store.removeClip(id)"
                @remove-lane="store.removeLane(lane.id)"
                @open-plugins="(path) => { pluginLaneIndex = idx; pluginPreselect = path ?? null }"
                @revoice="openRevoice(idx)"
              />
            </div>
          </div>
          </PanelFrame>
          </template>
        </div>

        <!-- The dock. One window at a time, chosen from the strip at the
             top the way FL does it - so the buttons up there are the
             dock's tabs and there is no second row of them down here. -->
        <section
          v-if="dockTab && !store.loading && !loadingAudio"
          class="flex shrink-0 flex-col border-t border-black"
          :style="{ height: dockHeight + 'px' }"
        >
          <!-- Draggable. A dock you cannot resize is the thing people
               complain about in every DAW that has one. -->
          <div
            class="h-1 shrink-0 cursor-row-resize bg-black transition-colors hover:bg-accent1/60"
            @pointerdown="startDockResize"
          ></div>
          <div v-show="dockTab === 'mixer'" class="flex min-h-0 flex-1 flex-col p-1.5">
            <PanelFrame
              :collapsible="false"
              class="min-h-0 flex-1"
                :title="t('dawMixer.title')"
              accent="var(--color-accent2)"
              :badge="`${store.project.lanes.length + 1} ${t('editor.stripsShort')}`"
              dense
            >
              <template #actions>
                <select
                  v-model="exportFormat"
                  class="rounded bg-panel-2 px-1.5 py-0.5 text-[10px] text-text"
                >
                  <option value="wav">WAV</option>
                  <option value="mp3">MP3</option>
                </select>
                <button
                  type="button"
                  class="accent-gradient rounded px-2 py-0.5 text-[10px] disabled:opacity-50"
                  :disabled="exporting"
                  @click="doExport"
                >{{ exporting ? t('editor.exporting') : t('editor.export') }}</button>
              </template>
              <MixerPanel
                class="min-w-0 flex-1 border-0 bg-transparent"
                :lanes="store.project.lanes"
                :master="store.project.master"
                :lane-levels="laneLevels"
                :master-level="masterLevel"
                :selected="mixerSelected"
                @select="mixerSelected = $event"
                @update-lane="onMixerLane"
                @update-master="store.updateMasterSettings($event)"
                @open-plugins="(p) => { pluginLaneIndex = p.laneIndex; pluginPreselect = p.pluginPath ?? null }"
              />
            </PanelFrame>
            <p v-if="exportError" class="rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">{{ exportError }}</p>
            <p v-if="exportedOk" class="rounded-lg bg-panel-2 p-2 text-xs text-text-dim">{{ t('editor.exportedAsNewTrack') }}</p>
          </div>
          <div v-show="dockTab === 'rack'" class="flex min-h-0 flex-1 flex-col p-1.5">
            <PanelFrame
              :collapsible="false"
              class="min-h-0 flex-1"
                      :title="t('rack.title')"
              accent="var(--color-status-queued)"
              :badge="rack.channels.length ? t('rack.channelCount', { n: rack.channels.length }) : ''"
              dense
            >
              <ChannelRackPanel
                ref="rackComponent"
                :rack="rack"
                :master="store.project.master"
                :buffers="buffers"
                :bpm="projectBpm(store.project)"
                :bouncing="rackBouncing"
                @add-channel="addRackChannel"
                @change="store.commitSnapshot()"
                @bounce="bounceRack"
                @open-plugins="(id) => (rackChannelForPlugins = id)"
              />
            </PanelFrame>
          </div>
        </section>
      </main>

      <!-- Tools. Things you consult while looking at the song, not things
           you scroll past to reach it. -->
      <aside
        v-if="toolsOpen"
        class="hidden w-[340px] shrink-0 flex-col border-l border-black xl:flex"
      >
        <div class="rack-strip flex shrink-0 items-center justify-between px-2 py-1">
          <span class="engraved text-[9px] font-semibold uppercase text-text-faint">
            {{ t('editor.toolsRail') }}
          </span>
          <button
            type="button"
            class="px-1 text-[11px] text-text-faint hover:text-text"
            :title="t('common.close')"
            @click="toolsOpen = false"
          >&#10005;</button>
        </div>
        <div class="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-1.5">
        <PanelFrame :title="t('coproducer.title')" accent="#a78bfa" start-collapsed>
          <CoProducerPanel
            @master="toolsOpen = true; masteringPanel?.reveal()"
            @write-progression="writeProgression"
          />
        </PanelFrame>

        <PanelFrame ref="masteringPanel" :title="t('mastering.title')" accent="#f472b6" start-collapsed>
          <MasteringPanel />
        </PanelFrame>

        <PanelFrame :title="t('engine.title')" accent="#34d399" start-collapsed>
          <AudioEnginePanel />
        </PanelFrame>

        <PanelFrame
          ref="collabPanel"
          :title="t('collab.title')"
          accent="#38bdf8"
          :badge="collab.live ? String(collab.peers.length) : ''"
          start-collapsed
        >
          <CollabPanel />
        </PanelFrame>

        <PanelFrame :title="t('designer.title')" accent="#f0b429" start-collapsed>
          <EffectDesigner />
        </PanelFrame>

        <PanelFrame :title="t('genFill.title')" accent="#a78bfa" start-collapsed>
          <GenerativeFill
          :lanes="store.project.lanes"
          :buffers="buffers"
          :loop-start="loop.startSec"
          :loop-end="loop.endSec"
          :loop-enabled="loop.enabled"
          :total-duration="store.totalDuration"
          :engine-ready="aceRunning"
          class="mt-4"
          @replace-lane="onReplaceLane"
          @new-part="onNewPart"
            @set-range="(r) => setLoop({ enabled: true, startSec: r.startSec, endSec: r.endSec })"
          />
        </PanelFrame>
        </div>
      </aside>
    </div>

    <LibraryPicker v-if="pickerOpenForNewLane" @pick="onPickForNewLane" @close="pickerOpenForNewLane = false" />
  </div>

    <div
      v-if="revoiceLaneIndex != null"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      @click.self="revoiceLaneIndex = null"
    >
      <div class="w-full max-w-md rounded-xl border border-border bg-panel p-4">
        <h3 class="text-sm font-semibold text-text">{{ t('revoice.title') }}</h3>
        <p class="mt-1 text-xs text-text-dim">{{ t('revoice.note') }}</p>

        <ul v-if="voiceList.length" class="mt-3 max-h-60 space-y-1 overflow-y-auto">
          <li v-for="voice in voiceList" :key="voice.name">
            <button
              type="button"
              class="w-full rounded-lg border border-border px-3 py-2 text-left text-sm text-text hover:border-accent1/60 hover:bg-panel-2 disabled:opacity-50"
              :disabled="revoiceBusy"
              @click="applyRevoice(voice.name)"
            >
              {{ voice.name }}
            </button>
          </li>
        </ul>
        <p v-else class="mt-3 text-xs text-text-dim">{{ t('revoice.noVoices') }}</p>

        <p v-if="revoiceBusy" class="mt-3 text-xs text-text-dim">{{ t('revoice.working') }}</p>
        <p v-if="revoiceError" class="mt-2 rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">
          {{ revoiceError }}
        </p>

        <button type="button" class="mt-3 text-xs text-text-dim hover:text-text" @click="revoiceLaneIndex = null">
          {{ t('common.cancel') }}
        </button>
      </div>
    </div>

    <PluginRack
      v-if="rackPluginChannel"
      :track-title="rackPluginChannel.name"
      :slot-key="`rack-${rackPluginChannel.id}`"
      @close="rackChannelForPlugins = null"
      @apply="applyPluginToChannel"
    />
    <PluginRack
      v-if="pluginLaneIndex != null"
      :track-title="pluginLaneTitle"
      :slot-key="`lane-${pluginLaneIndex}`"
      :preselect-path="pluginPreselect"
      @close="pluginLaneIndex = null"
      @apply="applyPlugin"
    />
    <p v-if="pluginBusy" class="fixed bottom-4 right-4 z-50 rounded-lg border border-border bg-panel px-3 py-2 text-xs text-text">
      {{ t('plugins.printing') }}
    </p>
    <p v-if="pluginError" class="fixed bottom-4 right-4 z-50 max-w-sm rounded-lg bg-status-failed/20 px-3 py-2 text-xs text-status-failed">
      {{ pluginError }}
    </p>
</template>
