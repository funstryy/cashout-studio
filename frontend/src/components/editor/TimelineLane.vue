<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { defaultChannelSettings } from '../../audio/mixerEngine'
import type { ChannelSettings } from '../../audio/mixerEngine'
import { lanePlugins } from '../../audio/timelineTypes'
import type { AppliedPlugin, Clip, TimelineLane } from '../../audio/timelineTypes'
import ChannelStrip from '../shared/ChannelStrip.vue'
import TimelineClip from './TimelineClip.vue'

const props = defineProps<{
  lane: TimelineLane
  pxPerSecond: number
  buffers: Map<string, AudioBuffer>
  snapCandidates: number[]
  selectedClipId: string | null
  widthPx: number
  level?: { peak: number; clipping: boolean }
  /** Live take in progress on this lane, drawn as it grows. The real clip
   * only exists once recording stops and the audio has been encoded. */
  recording?: { startSec: number; nowSec: number } | null
}>()

const emit = defineEmits<{
  'update:settings': [settings: ChannelSettings]
  rename: [name: string]
  moveClip: [payload: { clipId: string; timelineStart: number }]
  trimClip: [payload: { clipId: string; trimStart: number; trimEnd: number; timelineStart: number }]
  dragEnd: []
  /** The second value is where on the timeline the click landed, in
   *  seconds - the slice tool needs the point, not just the clip. */
  selectClip: [clipId: string, atSec: number]
  removeClip: [clipId: string]
  removeLane: []
  openPlugins: [pluginPath?: string]
  revoice: []
  dropAudio: [payload: { file: File; timelineStart: number }]
}>()

const { t } = useI18n()

const isDragOver = ref(false)

/*
 * The track header.
 *
 * It was a 220px-tall form: a text field, a full channel strip with
 * labelled sliders, and four full-width buttons - per track. Eight tracks
 * filled the window with controls and left no room for the song, which is
 * the opposite of what an arrangement view is for. Every workstation solves
 * this the same way and it is not a matter of taste: the header is one row
 * high, carries only the controls you touch while the transport is
 * rolling - name, mute, solo, level, pan - and everything else lives one
 * click away.
 *
 * So the strip is still here, in full, behind the ⋯ button. Nothing was
 * removed; it stopped being permanently on screen.
 */
const menuOpen = ref(false)

/** Patches one field of the channel without disturbing the rest. */
function setChannel(patch: Partial<ChannelSettings>) {
  emit('update:settings', { ...defaultChannelSettings(), ...props.lane.settings, ...patch })
}

const volume = computed({
  get: () => props.lane.settings?.volume ?? 1,
  set: (v: number) => setChannel({ volume: v }),
})
const pan = computed({
  get: () => props.lane.settings?.pan ?? 0,
  set: (v: number) => setChannel({ pan: v }),
})
const muted = computed({
  get: () => props.lane.settings?.muted ?? false,
  set: (v: boolean) => setChannel({ muted: v }),
})
const solo = computed({
  get: () => props.lane.settings?.solo ?? false,
  set: (v: boolean) => setChannel({ solo: v }),
})

function onDrop(e: DragEvent) {
  isDragOver.value = false
  const file = e.dataTransfer?.files?.[0]
  if (!file) return
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  const x = Math.max(0, e.clientX - rect.left)
  const timelineStart = Math.max(0, x / props.pxPerSecond)
  emit('dropAudio', { file, timelineStart })
}

/** What has been printed onto this track, oldest first. */
const applied = computed<AppliedPlugin[]>(() => lanePlugins(props.lane))

function appliedTitle(plugin: AppliedPlugin): string {
  const when = new Date(plugin.appliedAt)
  const stamp = Number.isNaN(when.valueOf()) ? '' : when.toLocaleString()
  return `${plugin.name}${stamp ? ` · ${stamp}` : ''}\n${t('plugins.reopenHint')}`
}

/** Where a click on a clip falls on the project timeline. Falls back to the
 *  clip's start when the event carries no coordinates. */
function clipPointSec(clip: Clip, event?: MouseEvent): number {
  if (!event) return clip.timelineStart
  const target = (event.currentTarget as HTMLElement | null)?.getBoundingClientRect()
  if (!target) return clip.timelineStart
  return clip.timelineStart + (event.clientX - target.left) / props.pxPerSecond
}

function bufferFor(clip: Clip): AudioBuffer | null {
  return props.buffers.get(clip.sourceUrl) ?? null
}
</script>

<template>
  <div class="flex border-b border-border/60">
    <!-- One row high, like every other workstation. See the note on
         menuOpen above for why this is not a stylistic choice. -->
    <div class="relative flex h-[72px] w-56 shrink-0 flex-col justify-center gap-1 border-r border-black px-1.5">
      <div class="flex items-center gap-1">
        <input
          :value="lane.name"
          :placeholder="t('timeline.trackNamePlaceholder')"
          class="min-w-0 flex-1 rounded-[1px] bg-transparent px-1 py-0.5 text-[11px] font-medium text-text outline-none focus:bg-black/50"
          @input="emit('rename', ($event.target as HTMLInputElement).value)"
        />
        <button
          type="button"
          class="key px-1 py-0 text-[11px] leading-none"
          :class="menuOpen ? 'key-on' : ''"
          :title="t('timeline.moreTitle')"
          @click="menuOpen = !menuOpen"
        >&#8943;</button>
      </div>

      <div class="flex items-center gap-1">
        <button
          type="button"
          class="key h-[15px] w-[17px] text-[9px] font-semibold leading-none"
          :class="muted ? 'key-on' : ''"
          style="--key-accent: var(--color-status-failed)"
          :title="t('channelStrip.muteTitle')"
          @click="muted = !muted"
        >M</button>
        <button
          type="button"
          class="key h-[15px] w-[17px] text-[9px] font-semibold leading-none"
          :class="solo ? 'key-on' : ''"
          style="--key-accent: var(--color-status-queued)"
          :title="t('channelStrip.soloTitle')"
          @click="solo = !solo"
        >S</button>
        <input
          v-model.number="volume"
          type="range" min="0" max="1.5" step="0.01"
          class="min-w-0 flex-1"
          :title="t('channelStrip.volume', { value: Math.round(volume * 100) })"
        />
        <span class="readout-dim w-8 shrink-0 text-right text-[9px]">
          {{ Math.round(volume * 100) }}%
        </span>
      </div>

      <div class="flex items-center gap-1">
        <span class="engraved shrink-0 text-[8px] text-text-faint">PAN</span>
        <input
          v-model.number="pan"
          type="range" min="-1" max="1" step="0.01"
          class="w-12 shrink-0"
          :title="t('channelStrip.pan', { value: pan.toFixed(2) })"
        />
        <!-- The meter sits in the header rather than in the strip, because
             it is the one thing here you read continuously. -->
        <div class="meter-well h-[7px] min-w-0 flex-1">
          <div
            class="meter-fill transition-all duration-75 ease-out"
            :style="{
              width: `${Math.min(100, Math.round((level?.peak ?? 0) * 100))}%`,
              background: level?.clipping ? 'var(--color-status-failed)' : 'var(--color-accent1)',
              color: level?.clipping ? 'var(--color-status-failed)' : 'var(--color-accent1)',
            }"
          />
        </div>
      </div>

      <!-- Everything the header used to show permanently. -->
      <div
        v-if="menuOpen"
        class="card absolute left-1 top-[70px] z-30 max-h-[60vh] w-64 space-y-1.5 overflow-y-auto p-2 shadow-2xl"
      >
        <ChannelStrip
          :model-value="lane.settings"
          label=""
          show-pan-mute-solo
          :level="level?.peak || 0"
          :clipping="level?.clipping || false"
          @update:model-value="(v) => emit('update:settings', v)"
          @reset="emit('update:settings', defaultChannelSettings())"
        />
        <button type="button" class="key w-full py-1 text-[11px]" @click="menuOpen = false; emit('openPlugins')">
          {{ t('plugins.insert') }}
        </button>
        <button type="button" class="key w-full py-1 text-[11px]" @click="menuOpen = false; emit('revoice')">
          {{ t('revoice.action') }}
        </button>

        <!-- What this track has been printed through. Clicking one reopens it
             with the settings that print used. -->
        <div v-if="applied.length" class="max-h-20 space-y-0.5 overflow-y-auto">
          <button
            v-for="(plugin, index) in applied"
            :key="`${plugin.stateKey}-${index}`"
            type="button"
            class="flex w-full items-center gap-1 rounded-[1px] border border-accent1/30 bg-accent1/10 px-1.5 py-0.5 text-left text-[10px] text-text-dim transition-colors hover:border-accent1/60 hover:text-text"
            :title="appliedTitle(plugin)"
            @click="menuOpen = false; emit('openPlugins', plugin.path)"
          >
            <span class="shrink-0 tabular-nums opacity-60">{{ index + 1 }}</span>
            <span class="truncate">{{ plugin.name }}</span>
          </button>
        </div>
        <button
          type="button"
          class="key w-full py-1 text-[11px] text-status-failed"
          style="--key-accent: var(--color-status-failed)"
          @click="emit('removeLane')"
        >
          {{ t('timeline.removeTrack') }}
        </button>
      </div>
    </div>

    <div
      class="relative min-h-20 flex-1 overflow-hidden transition-colors"
      :class="isDragOver ? 'bg-accent1/10 ring-2 ring-inset ring-accent1' : ''"
      :style="{ minWidth: widthPx + 'px' }"
      @dragover.prevent="isDragOver = true"
      @dragleave="isDragOver = false"
      @drop.prevent="onDrop"
    >
      <!-- Drop hint overlay -->
      <div v-if="isDragOver" class="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
        <span class="rounded-lg bg-accent1/80 px-4 py-2 text-sm font-medium text-white shadow-lg">
          {{ t('timeline.dropHint') }}
        </span>
      </div>
      <TimelineClip
        v-for="clip in lane.clips"
        :key="clip.id"
        :clip="clip"
        :px-per-second="pxPerSecond"
        :buffer="bufferFor(clip)"
        :snap-candidates="snapCandidates"
        :selected="clip.id === selectedClipId"
        @move="(timelineStart) => emit('moveClip', { clipId: clip.id, timelineStart })"
        @trim="(payload) => emit('trimClip', { clipId: clip.id, ...payload })"
        @drag-end="emit('dragEnd')"
        @select="(e?: MouseEvent) => emit('selectClip', clip.id, clipPointSec(clip, e))"
        @remove="emit('removeClip', clip.id)"
      />

      <div
        v-if="recording && recording.nowSec > recording.startSec"
        class="pointer-events-none absolute top-1 bottom-1 rounded border border-status-failed/70 bg-status-failed/20"
        :style="{
          left: recording.startSec * pxPerSecond + 'px',
          width: Math.max(2, (recording.nowSec - recording.startSec) * pxPerSecond) + 'px',
        }"
      >
        <span class="absolute left-1 top-1 flex items-center gap-1 text-[10px] font-medium text-status-failed">
          <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-status-failed"></span>
          {{ t('editor.recording') }}
        </span>
      </div>
    </div>
  </div>
</template>
