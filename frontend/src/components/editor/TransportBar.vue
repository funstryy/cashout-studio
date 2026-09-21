<script setup lang="ts">
/**
 * The transport strip.
 *
 * One dense row across the top, the way every hardware sequencer and every
 * DAW since has done it: play, stop, record, the clock, tempo, and the
 * toggles you reach for mid-take. It replaces three separate wrapped rows of
 * buttons, which between them ate about 120px of vertical space that the
 * arrangement wanted.
 *
 * The clock is the one deliberately oversized element. It is the thing you
 * glance at while your hands are busy, so it is set large, in tabular
 * figures so the digits do not shuffle, and lit rather than printed.
 */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{
  playing: boolean
  recording: boolean
  armed: boolean
  positionSec: number
  totalSec: number
  bpm: number
  loopEnabled: boolean
  metronome: boolean
  dirty: boolean
  saving: boolean
  projectName: string
  canUndo: boolean
  canRedo: boolean
  /** Live session state, shown here rather than only in the panel: a solo
   *  project has no reason to have the panel open, so without this there is
   *  nothing on screen telling you a friend can be invited at all. */
  collabLive: boolean
  collabPeers: number
}>()

const emit = defineEmits<{
  play: []
  pause: []
  stop: []
  record: []
  'update:bpm': [value: number]
  'update:projectName': [value: string]
  toggleLoop: []
  toggleMetronome: []
  save: []
  undo: []
  redo: []
  collab: []
}>()

const { t } = useI18n()

/** bars:beats:sixteenths as well as the clock - which of the two you want
 *  depends on whether you are editing to a grid or to a vocal. */
const musical = computed(() => {
  const beats = (props.positionSec * props.bpm) / 60
  const bar = Math.floor(beats / 4) + 1
  const beat = Math.floor(beats % 4) + 1
  const sixteenth = Math.floor(((beats % 1) * 4)) + 1
  return `${bar}:${beat}:${sixteenth}`
})

const clock = computed(() => {
  const total = Math.max(0, props.positionSec)
  const minutes = Math.floor(total / 60)
  const seconds = Math.floor(total % 60)
  const hundredths = Math.floor((total % 1) * 100)
  return `${minutes}:${String(seconds).padStart(2, '0')}.${String(hundredths).padStart(2, '0')}`
})
</script>

<template>
  <!-- The transport is the face of the machine, so it is built as one: a
       brushed strip with keys standing proud of it and the clock recessed
       into a well. Everything here was a flat rounded rectangle before. -->
  <div class="rack-strip flex flex-wrap items-center gap-2 border border-border px-2 py-1.5">
    <!-- Transport -->
    <div class="flex items-center gap-1">
      <button
        type="button"
        class="key flex h-8 w-9 items-center justify-center"
        :class="playing ? 'key-on' : ''"
        :title="t('editor.play')"
        @click="playing ? emit('pause') : emit('play')"
      >
        <svg viewBox="0 0 24 24" class="h-4 w-4" aria-hidden="true">
          <path v-if="!playing" d="M8 5v14l11-7z" fill="currentColor" />
          <path v-else d="M7 5h4v14H7zm6 0h4v14h-4z" fill="currentColor" />
        </svg>
      </button>
      <button
        type="button"
        class="key flex h-8 w-9 items-center justify-center"
        :title="t('editor.stop')"
        @click="emit('stop')"
      >
        <svg viewBox="0 0 24 24" class="h-3.5 w-3.5"><rect x="6" y="6" width="12" height="12" fill="currentColor" /></svg>
      </button>
      <!-- Record is the one key with its own lamp colour. Armed is the lamp
           at half brightness, which is what "ready, not running" looks like
           on a desk. -->
      <button
        type="button"
        class="key flex h-8 w-9 items-center justify-center"
        :class="recording ? 'key-on breathe' : armed ? 'key-on opacity-70' : 'hover:text-status-failed'"
        style="--key-accent: var(--color-status-failed)"
        :title="t('editor.record')"
        @click="emit('record')"
      >
        <svg viewBox="0 0 24 24" class="h-3.5 w-3.5"><circle cx="12" cy="12" r="6" fill="currentColor" /></svg>
      </button>
    </div>

    <!-- Clock. The one thing on this bar meant to be read from across the
         room, so it gets the size, the glow, and a well of its own. The
         ghost digits behind it are the unlit segments of a real display -
         without them a seven-segment readout looks like text. -->
    <div class="well relative flex items-baseline gap-2.5 px-3 py-1 font-mono">
      <span class="relative text-xl leading-none">
        <span class="pointer-events-none absolute inset-0 tabular-nums text-white/[0.045]" aria-hidden="true">
          {{ clock.replace(/[0-9]/g, '8') }}
        </span>
        <span class="readout relative tabular-nums">{{ clock }}</span>
      </span>
      <span class="readout-dim text-[11px] leading-none">{{ musical }}</span>
    </div>

    <!-- Tempo -->
    <label class="well flex items-center gap-1.5 px-2 py-1">
      <span class="engraved text-[9px] uppercase text-text-faint">{{ t('editor.bpm') }}</span>
      <input
        :value="bpm"
        type="number" min="20" max="300"
        class="readout w-12 bg-transparent text-sm font-semibold outline-none"
        @input="emit('update:bpm', Number(($event.target as HTMLInputElement).value))"
      />
    </label>

    <!-- Toggles -->
    <div class="flex items-center gap-1">
      <button
        type="button"
        class="key px-2 py-1.5 text-[10px] font-semibold uppercase tracking-wider"
        :class="loopEnabled ? 'key-on' : ''"
        style="--key-accent: var(--color-accent2)"
        @click="emit('toggleLoop')"
      >{{ t('editor.loop') }}</button>
      <button
        type="button"
        class="key px-2 py-1.5 text-[10px] font-semibold uppercase tracking-wider"
        :class="metronome ? 'key-on' : ''"
        @click="emit('toggleMetronome')"
      >{{ t('editor.click') }}</button>
    </div>

    <!-- History -->
    <div class="flex items-center gap-0.5">
      <button
        type="button"
        class="key px-2 py-1.5 text-[11px]"
        :disabled="!canUndo"
        :title="t('editor.undo')"
        @click="emit('undo')"
      >↶</button>
      <button
        type="button"
        class="key px-2 py-1.5 text-[11px]"
        :disabled="!canRedo"
        :title="t('editor.redo')"
        @click="emit('redo')"
      >↷</button>
    </div>

    <!-- Session -->
    <button
      type="button"
      class="key ml-auto flex items-center gap-1.5 px-2 py-1.5 text-[11px]"
      :class="collabLive ? 'key-on' : ''"
      :title="t('collab.transportTitle')"
      @click="emit('collab')"
    >
      <span
        class="led h-1.5 w-1.5"
        :class="collabLive ? 'animate-pulse' : ''"
        :style="{
          background: collabLive ? 'var(--color-accent1)' : '#10161d',
          color: collabLive ? 'var(--color-accent1)' : 'transparent',
        }"
      ></span>
      {{ collabLive ? t('collab.peerCount', { n: collabPeers }) : t('collab.invite') }}
    </button>

    <!-- Project -->
    <div class="flex items-center gap-1.5">
      <input
        :value="projectName"
        class="well w-40 px-2 py-1.5 text-xs text-text outline-none focus:text-text"
        @input="emit('update:projectName', ($event.target as HTMLInputElement).value)"
      />
      <button
        type="button"
        class="key px-3 py-1.5 text-xs font-medium"
        :class="dirty ? 'key-on' : ''"
        :disabled="saving"
        @click="emit('save')"
      >
        {{ saving ? t('editor.saving') : dirty ? t('common.save') : t('editor.saved') }}
      </button>
    </div>
  </div>
</template>
