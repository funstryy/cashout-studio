<script setup lang="ts">
/**
 * The channel rack.
 *
 * One row per sample, one square per step. It plays on its own loop, at the
 * project's tempo, independently of the timeline transport - a pattern is
 * something you build while the arrangement sits still, and stopping the
 * whole song to audition a hi-hat is what makes people stop using a rack.
 *
 * Bouncing is what connects the two: the pattern renders through the same
 * graph it was heard through and lands on the timeline as an ordinary clip.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  BAR_CHOICES,
  STEPS_PER_BAR_CHOICES,
  rackTotalSteps,
  resizeChannelSteps,
  startRack,
} from '../../audio/channelRack'
import { channelMode, channelNotes } from '../../audio/timelineTypes'
import PianoRoll from './PianoRoll.vue'
import type { RackHandle } from '../../audio/channelRack'
import type { ChannelRack, RackChannel } from '../../audio/timelineTypes'
import type { MasterSettings } from '../../audio/mixerEngine'
import { getSharedAudioCtx } from '../../composables/audioPlayback'
import LibraryPicker from './LibraryPicker.vue'

const props = defineProps<{
  rack: ChannelRack
  master: MasterSettings
  buffers: Map<string, AudioBuffer>
  bpm: number
  bouncing?: boolean
}>()

const emit = defineEmits<{
  /** A sample was chosen; the page decodes it before the channel appears. */
  addChannel: [payload: { sourceUrl: string; sourceLabel: string }]
  change: []
  bounce: [repeats: number]
  openPlugins: [channelId: string]
}>()

const { t } = useI18n()

const playing = ref(false)
const currentStep = ref(-1)
const pickerOpen = ref(false)
const repeats = ref(1)
let handle: RackHandle | null = null

const totalSteps = computed(() => rackTotalSteps(props.rack))
const stepIndexes = computed(() => Array.from({ length: totalSteps.value }, (_, i) => i))

/** Every fourth square starts a beat, which is what makes a grid readable. */
function isBeatStart(index: number): boolean {
  return index % (props.rack.stepsPerBar / 4) === 0
}

function isBarStart(index: number): boolean {
  return index % props.rack.stepsPerBar === 0
}

function stop() {
  handle?.stop()
  handle = null
  playing.value = false
  currentStep.value = -1
}

function play() {
  if (playing.value) {
    stop()
    return
  }
  if (!props.rack.channels.length) return
  handle = startRack({
    ctx: getSharedAudioCtx(),
    rack: props.rack,
    master: props.master,
    buffers: props.buffers,
    bpm: props.bpm,
    onStep: (step) => (currentStep.value = step),
  })
  playing.value = true
}

function toggleStep(channel: RackChannel, index: number) {
  channel.steps[index] = !channel.steps[index]
  emit('change')
}

/** Drag across a row to paint several steps at once, as a rack should. */
const painting = ref<boolean | null>(null)

/** Which channel's roll is open, if any. One at a time: two rolls side by
 *  side is a window manager, not a sequencer. */
const rollChannelId = ref<string | null>(null)

/**
 * Opened from the toolbar rather than from a channel's own button.
 *
 * FL's F7 opens the piano roll for whatever channel is selected, not for a
 * channel you have to go and find first. With nothing selected the first
 * one is the right guess - it is the only one there is, in the case where
 * this matters.
 */
function openRoll(channelId?: string) {
  const target = channelId ?? rollChannelId.value ?? props.rack.channels[0]?.id ?? null
  rollChannelId.value = target
  return target !== null
}

const rollChannel = computed(() =>
  props.rack.channels.find((c) => c.id === rollChannelId.value) ?? null,
)

function toggleRoll(channel: RackChannel) {
  rollChannelId.value = rollChannelId.value === channel.id ? null : channel.id
}

/** A note channel's step row stops being editable and becomes a read-only
 *  summary: the squares show which steps the notes fall on, so the row still
 *  says something useful, but clicking it would edit data that no longer
 *  plays. */
function noteSteps(channel: RackChannel): Set<number> {
  const occupied = new Set<number>()
  for (const note of channelNotes(channel)) {
    const from = Math.floor(note.start)
    const to = Math.ceil(note.start + note.length)
    for (let step = from; step < to; step++) occupied.add(step)
  }
  return occupied
}

function isNoteChannel(channel: RackChannel): boolean {
  return channelMode(channel) === 'notes'
}

function startPaint(channel: RackChannel, index: number) {
  painting.value = !channel.steps[index]
  toggleStep(channel, index)
}

function paintOver(channel: RackChannel, index: number) {
  if (painting.value === null) return
  if (channel.steps[index] === painting.value) return
  channel.steps[index] = painting.value
  emit('change')
}

function endPaint() {
  painting.value = null
}

function clearChannel(channel: RackChannel) {
  channel.steps = channel.steps.map(() => false)
  emit('change')
}

function removeChannel(channel: RackChannel) {
  const index = props.rack.channels.indexOf(channel)
  if (index !== -1) props.rack.channels.splice(index, 1)
  emit('change')
}

function onPick(payload: { sourceUrl: string; sourceLabel: string }) {
  pickerOpen.value = false
  emit('addChannel', payload)
}

function setLayout(stepsPerBar: number, bars: number) {
  const wasPlaying = playing.value
  stop()
  props.rack.stepsPerBar = stepsPerBar
  props.rack.bars = bars
  resizeChannelSteps(props.rack)
  emit('change')
  if (wasPlaying) play()
}

// Tempo and pattern length change what is already scheduled, so the loop is
// restarted rather than left running against the old grid.
watch(() => props.bpm, () => {
  if (playing.value) {
    stop()
    play()
  }
})

onBeforeUnmount(stop)

defineExpose({ stop, openRoll })
</script>

<template>
  <section class="" @pointerup="endPaint" @pointerleave="endPaint">
    <header class="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
      <button
        type="button"
        class="rounded-lg px-3 py-1 text-xs font-medium text-white disabled:opacity-40"
        :class="playing ? 'bg-status-failed' : 'accent-gradient'"
        :disabled="!rack.channels.length"
        @click="play"
      >
        {{ playing ? t('rack.stop') : t('rack.play') }}
      </button>

      <button
        type="button"
        class="rounded-lg border border-border px-3 py-1 text-xs text-text-dim hover:text-text"
        @click="pickerOpen = true"
      >
        {{ t('rack.addChannel') }}
      </button>

      <label class="flex items-center gap-1 text-xs text-text-dim">
        {{ t('rack.steps') }}
        <select
          :value="rack.stepsPerBar"
          class="rounded border border-border bg-panel-2 px-1 py-0.5 text-xs text-text"
          @change="setLayout(Number(($event.target as HTMLSelectElement).value), rack.bars)"
        >
          <option v-for="choice in STEPS_PER_BAR_CHOICES" :key="choice" :value="choice">{{ choice }}</option>
        </select>
      </label>

      <label class="flex items-center gap-1 text-xs text-text-dim">
        {{ t('rack.bars') }}
        <select
          :value="rack.bars"
          class="rounded border border-border bg-panel-2 px-1 py-0.5 text-xs text-text"
          @change="setLayout(rack.stepsPerBar, Number(($event.target as HTMLSelectElement).value))"
        >
          <option v-for="choice in BAR_CHOICES" :key="choice" :value="choice">{{ choice }}</option>
        </select>
      </label>

      <span class="text-xs text-text-dim">{{ t('rack.atBpm', { bpm }) }}</span>

      <div class="ml-auto flex items-center gap-2">
        <label class="flex items-center gap-1 text-xs text-text-dim">
          {{ t('rack.repeats') }}
          <input
            v-model.number="repeats"
            type="number" min="1" max="32"
            class="w-12 rounded border border-border bg-panel-2 px-1 py-0.5 text-xs text-text"
          />
        </label>
        <button
          type="button"
          class="rounded-lg border border-accent1/60 bg-accent1/10 px-3 py-1 text-xs text-text disabled:opacity-40"
          :disabled="!rack.channels.length || bouncing"
          @click="emit('bounce', Math.max(1, repeats))"
        >
          {{ bouncing ? t('rack.bouncing') : t('rack.bounce') }}
        </button>
      </div>
    </header>

    <p v-if="!rack.channels.length" class="px-3 py-4 text-xs text-text-dim">{{ t('rack.empty') }}</p>

    <div v-else class="overflow-x-auto">
      <div class="min-w-max">
        <!-- Step numbers, so a pattern can be talked about by position. -->
        <div class="flex items-center border-b border-border/60 px-2 py-1">
          <div class="w-56 shrink-0"></div>
          <div class="flex gap-0.5">
            <span
              v-for="index in stepIndexes"
              :key="index"
              class="w-6 text-center text-[9px] tabular-nums"
              :class="isBeatStart(index) ? 'text-text-dim' : 'text-transparent'"
            >
              {{ index + 1 }}
            </span>
          </div>
        </div>

        <div
          v-for="channel in rack.channels"
          :key="channel.id"
          class="flex items-center border-b border-border/40 px-2 py-1 last:border-b-0"
        >
          <div class="flex w-56 shrink-0 items-center gap-1 pr-2">
            <button
              type="button"
              class="w-6 shrink-0 rounded px-1 text-[10px] font-semibold"
              :class="channel.settings.muted ? 'bg-status-failed/30 text-status-failed' : 'text-text-dim hover:text-text'"
              :title="t('rack.mute')"
              @click="channel.settings.muted = !channel.settings.muted; emit('change')"
            >
              M
            </button>
            <button
              type="button"
              class="w-6 shrink-0 rounded px-1 text-[10px] font-semibold"
              :class="channel.settings.solo ? 'bg-accent1/30 text-text' : 'text-text-dim hover:text-text'"
              :title="t('rack.solo')"
              @click="channel.settings.solo = !channel.settings.solo; emit('change')"
            >
              S
            </button>
            <input
              v-model="channel.name"
              type="text"
              class="min-w-0 flex-1 rounded border border-border bg-panel-2 px-1.5 py-0.5 text-[11px] text-text"
              @change="emit('change')"
            />
            <input
              v-model.number="channel.settings.volume"
              type="range" min="0" max="1.5" step="0.01"
              class="w-12 shrink-0"
              :title="t('rack.volume')"
              @change="emit('change')"
            />
            <input
              v-model.number="channel.pitch"
              type="number" min="-24" max="24" step="1"
              class="w-10 shrink-0 rounded border border-border bg-panel-2 px-1 py-0.5 text-[10px] tabular-nums text-text"
              :title="t('rack.pitch')"
              @change="emit('change')"
            />
            <button
              type="button"
              class="shrink-0 rounded px-1 text-[10px] transition-colors"
              :class="rollChannelId === channel.id
                ? 'bg-accent1/20 text-accent1'
                : isNoteChannel(channel)
                  ? 'text-accent2 hover:text-text'
                  : 'text-text-dim hover:text-text'"
              :title="t('pianoRoll.open')"
              @click="toggleRoll(channel)"
            >
              ▦
            </button>
            <button
              type="button"
              class="shrink-0 px-1 text-[10px] text-text-dim hover:text-text"
              :title="t('plugins.insert')"
              @click="emit('openPlugins', channel.id)"
            >
              fx
            </button>
            <button
              type="button"
              class="shrink-0 px-1 text-[10px] text-text-dim hover:text-text"
              :title="t('rack.clear')"
              @click="clearChannel(channel)"
            >
              ⌫
            </button>
            <button
              type="button"
              class="shrink-0 px-1 text-[10px] text-status-failed/70 hover:text-status-failed"
              :title="t('rack.removeChannel')"
              @click="removeChannel(channel)"
            >
              ✕
            </button>
          </div>

          <div class="flex gap-0.5">
            <button
              v-for="index in stepIndexes"
              :key="index"
              type="button"
              class="h-6 w-6 rounded-sm border transition-colors"
              :class="[
                isNoteChannel(channel)
                  ? (noteSteps(channel).has(index)
                      ? 'cursor-default border-accent2/70 bg-accent2/60'
                      : 'cursor-default border-border/30 bg-panel-2/20')
                  : channel.steps[index]
                    ? 'border-accent1 bg-accent1'
                    : isBarStart(index)
                      ? 'border-border bg-panel-2'
                      : isBeatStart(index)
                        ? 'border-border/70 bg-panel-2/70'
                        : 'border-border/40 bg-panel-2/30',
                currentStep === index ? 'ring-1 ring-inset ring-text' : '',
              ]"
              :disabled="isNoteChannel(channel)"
              :title="isNoteChannel(channel) ? t('pianoRoll.stepsLocked') : undefined"
              @pointerdown="startPaint(channel, index)"
              @pointerenter="paintOver(channel, index)"
            ></button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="rollChannel" class="border-t border-border p-2">
      <PianoRoll
        :channel="rollChannel"
        :total-steps="totalSteps"
        :steps-per-bar="rack.stepsPerBar"
        :playhead="playing ? currentStep : null"
        @change="emit('change')"
        @close="rollChannelId = null"
      />
    </div>

    <LibraryPicker v-if="pickerOpen" @pick="onPick" @close="pickerOpen = false" />
  </section>
</template>
