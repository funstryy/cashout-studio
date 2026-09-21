<script setup lang="ts">
/**
 * The piano roll.
 *
 * Attached to a channel, the way FL Studio does it: a channel is a sound,
 * and the roll is where you say what that sound plays. Drawing the first
 * note flips the channel from step mode to note mode, so the grid row above
 * stops being what sounds and becomes a summary of it - the two can never
 * both play, which is the bug this design exists to prevent.
 *
 * Pitch is resampling, not time-stretching. A note an octave up plays the
 * sample at double speed and is therefore shorter and snappier. That is what
 * a sampler does, and it is what makes a pitched 808 behave the way people
 * expect rather than sounding like a stretched vowel.
 *
 * Interaction, chosen to match what a producer's hands already know:
 *   draw         click empty grid
 *   move         drag a note
 *   resize       drag its right edge
 *   delete       right-click, or select and press Delete
 *   velocity     drag in the lane underneath
 */
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { MIDDLE_C, channelNotes } from '../../audio/timelineTypes'
import type { RackChannel, RackNote } from '../../audio/timelineTypes'

const props = defineProps<{
  channel: RackChannel
  totalSteps: number
  stepsPerBar: number
  /** Where playback is, in steps, or null when stopped. */
  playhead: number | null
}>()

const emit = defineEmits<{ change: []; close: [] }>()
const { t } = useI18n()

// Two octaves either side of middle C covers a bassline and a lead without
// scrolling; the roll scrolls for anything beyond.
const LOW_KEY = 36
const HIGH_KEY = 84
const KEY_HEIGHT = 15
const VELOCITY_HEIGHT = 56

const stepWidth = ref(26)
const snap = ref(1)
const defaultLength = ref(1)
const selected = ref<string | null>(null)

const keys = computed(() => {
  const list: number[] = []
  for (let key = HIGH_KEY; key >= LOW_KEY; key--) list.push(key)
  return list
})

const NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
function keyName(key: number): string {
  return `${NAMES[key % 12]}${Math.floor(key / 12) - 1}`
}
function isBlack(key: number): boolean {
  return [1, 3, 6, 8, 10].includes(key % 12)
}

const gridWidth = computed(() => props.totalSteps * stepWidth.value)
const gridHeight = computed(() => keys.value.length * KEY_HEIGHT)

const notes = computed(() => channelNotes(props.channel))

function noteStyle(note: RackNote) {
  const top = (HIGH_KEY - note.key) * KEY_HEIGHT
  return {
    left: `${note.start * stepWidth.value}px`,
    width: `${Math.max(6, note.length * stepWidth.value - 1)}px`,
    top: `${top + 1}px`,
    height: `${KEY_HEIGHT - 2}px`,
    // Velocity reads as opacity: quieter notes recede, which is legible at a
    // glance in a way a number in a tooltip is not.
    opacity: String(0.4 + 0.6 * note.velocity),
  }
}

function ensureNotes(): RackNote[] {
  if (!props.channel.notes) props.channel.notes = []
  // Drawing anything commits the channel to notes; otherwise the step row
  // would keep sounding underneath and every note would double-trigger.
  props.channel.mode = 'notes'
  return props.channel.notes
}

function quantise(steps: number): number {
  if (snap.value <= 0) return Math.max(0, steps)
  return Math.max(0, Math.round(steps / snap.value) * snap.value)
}

function positionFrom(event: PointerEvent, element: HTMLElement) {
  const rect = element.getBoundingClientRect()
  const step = (event.clientX - rect.left) / stepWidth.value
  const key = HIGH_KEY - Math.floor((event.clientY - rect.top) / KEY_HEIGHT)
  return { step, key: Math.max(LOW_KEY, Math.min(HIGH_KEY, key)) }
}

function onGridDown(event: PointerEvent) {
  if (event.button !== 0) return
  const grid = event.currentTarget as HTMLElement
  const { step, key } = positionFrom(event, grid)
  const list = ensureNotes()
  const note: RackNote = {
    id: crypto.randomUUID(),
    start: quantise(step),
    length: defaultLength.value,
    key,
    velocity: 0.8,
  }
  list.push(note)
  selected.value = note.id
  emit('change')
  // Drag straight out of the click to set the length, like every roll does.
  beginResize(event, note)
}

type DragKind = 'move' | 'resize'
let drag: {
  kind: DragKind
  note: RackNote
  originStep: number
  originKey: number
  startStep: number
  startKey: number
  startLength: number
} | null = null

function beginMove(event: PointerEvent, note: RackNote) {
  if (event.button !== 0) return
  event.stopPropagation()
  const grid = (event.currentTarget as HTMLElement).parentElement as HTMLElement
  const { step, key } = positionFrom(event, grid)
  selected.value = note.id
  drag = {
    kind: 'move', note,
    originStep: step, originKey: key,
    startStep: note.start, startKey: note.key, startLength: note.length,
  }
  attach()
}

function beginResize(event: PointerEvent, note: RackNote) {
  event.stopPropagation()
  const target = event.currentTarget as HTMLElement
  const grid = (target.parentElement?.classList.contains('pr-grid')
    ? target.parentElement
    : target.parentElement?.parentElement) as HTMLElement
  const { step, key } = positionFrom(event, grid)
  drag = {
    kind: 'resize', note,
    originStep: step, originKey: key,
    startStep: note.start, startKey: note.key, startLength: note.length,
  }
  attach()
}

function onMove(event: PointerEvent) {
  if (!drag) return
  const grid = document.querySelector('.pr-grid') as HTMLElement | null
  if (!grid) return
  const { step, key } = positionFrom(event, grid)

  if (drag.kind === 'move') {
    drag.note.start = quantise(drag.startStep + (step - drag.originStep))
    drag.note.key = Math.max(LOW_KEY, Math.min(HIGH_KEY, drag.startKey + (key - drag.originKey)))
  } else {
    const length = quantise(drag.startLength + (step - drag.originStep))
    // A note has to be audible: zero length is a note that does not exist,
    // and the grid has no way to show it or grab it again.
    drag.note.length = Math.max(snap.value || 0.25, length)
    defaultLength.value = drag.note.length
  }
}

function onUp() {
  if (drag) {
    drag = null
    emit('change')
  }
  detach()
}

function attach() {
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp, { once: true })
}
function detach() {
  window.removeEventListener('pointermove', onMove)
}

function remove(note: RackNote) {
  const list = props.channel.notes
  if (!list) return
  const index = list.findIndex((n) => n.id === note.id)
  if (index !== -1) list.splice(index, 1)
  if (selected.value === note.id) selected.value = null
  emit('change')
}

function onKeydown(event: KeyboardEvent) {
  if ((event.key !== 'Delete' && event.key !== 'Backspace') || !selected.value) return
  const note = notes.value.find((n) => n.id === selected.value)
  if (note) {
    event.preventDefault()
    remove(note)
  }
}

function setVelocity(event: PointerEvent, note: RackNote) {
  const lane = event.currentTarget as HTMLElement
  const rect = lane.getBoundingClientRect()
  const value = 1 - (event.clientY - rect.top) / rect.height
  note.velocity = Math.max(0.05, Math.min(1, value))
  emit('change')
}

function clearAll() {
  if (props.channel.notes) props.channel.notes = []
  props.channel.mode = 'steps'
  emit('change')
}

/** Back to the step grid without losing what was drawn. */
function useSteps() {
  props.channel.mode = 'steps'
  emit('change')
}

onBeforeUnmount(detach)
</script>

<template>
  <section class="card overflow-hidden" tabindex="0" @keydown="onKeydown">
    <header class="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
      <h3 class="text-sm font-semibold text-text">{{ t('pianoRoll.title') }}</h3>
      <span class="min-w-0 truncate text-xs text-accent1">{{ channel.name }}</span>

      <label class="ml-2 flex items-center gap-1 text-[11px] text-text-dim">
        {{ t('pianoRoll.snap') }}
        <select v-model.number="snap" class="rounded border border-border bg-panel-2 px-1 py-0.5 text-[11px] text-text">
          <option :value="0">{{ t('pianoRoll.snapOff') }}</option>
          <option :value="0.25">1/4</option>
          <option :value="0.5">1/2</option>
          <option :value="1">1</option>
          <option :value="2">2</option>
          <option :value="4">4</option>
        </select>
      </label>

      <label class="flex items-center gap-1 text-[11px] text-text-dim">
        {{ t('pianoRoll.zoom') }}
        <input v-model.number="stepWidth" type="range" min="12" max="64" step="2" class="w-24" />
      </label>

      <span class="text-[11px] text-text-faint">{{ t('pianoRoll.noteCount', { n: notes.length }) }}</span>

      <div class="ml-auto flex items-center gap-2">
        <button
          v-if="channel.mode === 'notes'"
          type="button"
          class="rounded border border-border px-2 py-1 text-[11px] text-text-dim hover:text-text"
          :title="t('pianoRoll.useStepsHint')"
          @click="useSteps"
        >
          {{ t('pianoRoll.useSteps') }}
        </button>
        <button
          type="button"
          class="rounded border border-border px-2 py-1 text-[11px] text-text-dim hover:text-status-failed"
          @click="clearAll"
        >
          {{ t('pianoRoll.clear') }}
        </button>
        <button type="button" class="px-1 text-text-dim hover:text-text" @click="emit('close')">✕</button>
      </div>
    </header>

    <p v-if="channel.mode !== 'notes' && notes.length === 0" class="px-3 py-1.5 text-[11px] text-text-faint">
      {{ t('pianoRoll.emptyHint') }}
    </p>

    <div class="flex max-h-[26rem] overflow-auto">
      <!-- Keyboard -->
      <div class="sticky left-0 z-10 shrink-0 bg-panel">
        <div
          v-for="key in keys"
          :key="key"
          class="flex items-center justify-end border-b border-border/40 pr-1.5 text-[9px]"
          :class="isBlack(key)
            ? 'bg-panel-3 text-text-faint'
            : 'bg-panel-2 text-text-dim'"
          :style="{ height: `${KEY_HEIGHT}px`, width: '52px' }"
        >
          <span v-if="key % 12 === 0 || key === MIDDLE_C" :class="key === MIDDLE_C ? 'text-accent1' : ''">
            {{ keyName(key) }}
          </span>
        </div>
      </div>

      <div>
        <!-- Grid -->
        <div
          class="pr-grid relative"
          :style="{ width: `${gridWidth}px`, height: `${gridHeight}px` }"
          @pointerdown="onGridDown"
        >
          <!-- Rows: black keys shaded, so the octave is readable without labels -->
          <div
            v-for="(key, row) in keys"
            :key="`row-${key}`"
            class="absolute left-0 w-full border-b border-border/25"
            :class="isBlack(key) ? 'bg-panel-2/40' : ''"
            :style="{ top: `${row * KEY_HEIGHT}px`, height: `${KEY_HEIGHT}px` }"
          ></div>

          <!-- Bar and beat lines -->
          <div
            v-for="step in totalSteps"
            :key="`col-${step}`"
            class="absolute top-0 h-full"
            :class="(step - 1) % stepsPerBar === 0
              ? 'border-l border-border-strong'
              : (step - 1) % (stepsPerBar / 4) === 0
                ? 'border-l border-border/60'
                : 'border-l border-border/20'"
            :style="{ left: `${(step - 1) * stepWidth}px` }"
          ></div>

          <!-- Playhead -->
          <div
            v-if="playhead != null"
            class="pointer-events-none absolute top-0 z-20 h-full w-px bg-accent1"
            :style="{ left: `${playhead * stepWidth}px` }"
          ></div>

          <!-- Notes -->
          <div
            v-for="note in notes"
            :key="note.id"
            class="absolute rounded-[3px] border"
            :class="selected === note.id
              ? 'border-white bg-accent1 shadow-[0_0_0_1px_rgba(255,255,255,0.4)]'
              : 'border-accent1/60 bg-accent1/80'"
            :style="noteStyle(note)"
            @pointerdown="beginMove($event, note)"
            @contextmenu.prevent="remove(note)"
          >
            <!-- Resize handle -->
            <span
              class="absolute right-0 top-0 h-full w-1.5 cursor-ew-resize rounded-r-[3px] bg-white/25"
              @pointerdown.stop="beginResize($event, note)"
            ></span>
          </div>
        </div>

        <!-- Velocity lane -->
        <div
          class="relative border-t border-border bg-panel-2/50"
          :style="{ width: `${gridWidth}px`, height: `${VELOCITY_HEIGHT}px` }"
        >
          <div
            v-for="note in notes"
            :key="`vel-${note.id}`"
            class="absolute bottom-0 w-1.5 cursor-ns-resize rounded-t bg-accent2"
            :style="{
              left: `${note.start * stepWidth + 1}px`,
              height: `${note.velocity * VELOCITY_HEIGHT}px`,
            }"
            :title="t('pianoRoll.velocity', { n: Math.round(note.velocity * 100) })"
            @pointerdown.stop="setVelocity($event, note)"
          ></div>
          <span class="pointer-events-none absolute left-1 top-1 text-[9px] text-text-faint">
            {{ t('pianoRoll.velocityLane') }}
          </span>
        </div>
      </div>
    </div>
  </section>
</template>
