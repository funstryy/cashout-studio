<script setup lang="ts">
/**
 * The co-producer.
 *
 * It listens to the master bus while you work and says what it hears. Every
 * note carries the measurement behind it, because advice about a mix that
 * you cannot check is advice you have to take on faith - and the useful
 * half of "your low end is heavy" is the number that says how heavy.
 *
 * Notes that can be acted on carry a button that does the thing. Those go
 * through the editor store like any other edit, so undo works on them and
 * nothing happens behind your back.
 */
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiJson } from '../../api/http'
import { useEditorStore } from '../../stores/editor'

interface ActionSpec {
  kind: string
  label: string
  params: Record<string, unknown>
}
interface Note {
  id: string
  severity: 'critical' | 'warn' | 'info' | 'good'
  title: string
  detail: string
  evidence: string
  action?: ActionSpec
}

const emit = defineEmits<{
  master: [target: string]
  /** Chord tones to lay into the rack, as MIDI note numbers per bar. */
  writeProgression: [payload: { label: string; bars: number[][] }]
}>()

const { t } = useI18n()
const store = useEditorStore()

const listening = ref(false)
const notes = ref<Note[]>([])
const status = ref('')
const error = ref('')
const applied = ref<Set<string>>(new Set())

let timer: ReturnType<typeof setInterval> | null = null

// Loudest problems first. A clipping master and three empty lanes are not
// the same kind of news and should not be read in the order they were
// generated.
const RANK: Record<Note['severity'], number> = { critical: 0, warn: 1, info: 2, good: 3 }
const sorted = computed(() => [...notes.value].sort((a, b) => RANK[a.severity] - RANK[b.severity]))

const DOT: Record<Note['severity'], string> = {
  critical: 'bg-status-failed',
  warn: 'bg-status-queued',
  info: 'bg-accent2',
  good: 'bg-accent1',
}

async function poll() {
  try {
    const result = await apiJson<{
      listening: boolean
      silent?: boolean
      reason?: string
      notes: Note[]
    }>('/api/coproducer/listen', { project: store.project, seconds: 8 })

    notes.value = result.notes ?? []
    if (!result.listening) status.value = result.reason || t('coproducer.notHearing')
    else if (result.silent) status.value = t('coproducer.silence')
    else status.value = ''
    error.value = ''
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
    stop()
  }
}

function start() {
  listening.value = true
  void poll()
  // Every three seconds. The window it analyses is eight seconds long, so
  // faster than this mostly re-measures audio it has already judged.
  timer = setInterval(poll, 3000)
}

function stop() {
  listening.value = false
  if (timer) clearInterval(timer)
  timer = null
}

async function review() {
  error.value = ''
  try {
    const result = await apiJson<{ notes: Note[] }>('/api/coproducer/review', {
      project: store.project,
    })
    notes.value = result.notes
    status.value = t('coproducer.reviewed')
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

/**
 * Does the thing the note offered.
 *
 * Through the store, and with a snapshot first, so every one of these is a
 * single Ctrl+Z away. An assistant that edits your song had better be as
 * undoable as your own mouse.
 */
function apply(note: Note) {
  const action = note.action
  if (!action) return
  const params = action.params

  if (action.kind === 'master') {
    emit('master', String(params.target ?? 'streaming'))
    applied.value = new Set(applied.value).add(note.id)
    return
  }

  if (action.kind === 'write_progression') {
    const chords = (params.chords as { root: number; minor: boolean }[]) ?? []
    // Voiced around middle C, root in the bass an octave down. Nothing
    // clever - the point is a progression you can hear and then move, not
    // a finished part.
    const bars = chords.map((chord) => {
      const root = 60 + chord.root
      return [root - 12, root, root + (chord.minor ? 3 : 4), root + 7]
    })
    emit('writeProgression', { label: String(params.label ?? 'progression'), bars })
    applied.value = new Set(applied.value).add(note.id)
    return
  }

  store.snapshot()

  if (action.kind === 'set_lane_gain') {
    const lane = store.project.lanes[Number(params.lane)]
    if (lane) {
      const delta = Number(params.deltaDb ?? 0)
      const current = lane.settings.volume ?? 1
      // dB, not a multiplier: +6 dB is twice the amplitude, and the note
      // said dB because that is what the measurement was in.
      lane.settings.volume = Math.min(4, current * Math.pow(10, delta / 20))
    }
  } else if (action.kind === 'spread_pan') {
    const lanes = (params.lanes as number[]) ?? []
    // Alternating outward from the centre, leaving the first where it is:
    // whatever is on lane one is usually the thing that should stay put.
    const spread = [0, -0.4, 0.4, -0.7, 0.7, -0.25, 0.25]
    lanes.forEach((index, i) => {
      const lane = store.project.lanes[index]
      if (lane) lane.settings.pan = spread[i % spread.length]
    })
  } else if (action.kind === 'narrow') {
    const scale = Number(params.scale ?? 0.7)
    for (const lane of store.project.lanes) {
      lane.settings.pan = (lane.settings.pan ?? 0) * scale
    }
  }

  store.commitSnapshot()
  applied.value = new Set(applied.value).add(note.id)
}

onBeforeUnmount(stop)
</script>

<template>
  <div class="space-y-3">
    <p class="text-xs text-text-dim">{{ t('coproducer.intro') }}</p>

    <div class="flex flex-wrap items-center gap-1.5">
      <button
        type="button"
        class="rounded px-3 py-1.5 text-xs transition-colors"
        :class="listening ? 'bg-accent1/15 text-text' : 'accent-gradient'"
        @click="listening ? stop() : start()"
      >
        <span
          v-if="listening"
          class="mr-1.5 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent1"
        ></span>
        {{ listening ? t('coproducer.stop') : t('coproducer.listen') }}
      </button>
      <button
        type="button"
        class="rounded border border-border px-3 py-1.5 text-xs text-text-dim hover:text-text"
        @click="review"
      >{{ t('coproducer.review') }}</button>
      <span v-if="status" class="text-[11px] text-text-faint">{{ status }}</span>
    </div>

    <p v-if="listening && !notes.length" class="text-[11px] text-text-faint">
      {{ t('coproducer.warmingUp') }}
    </p>

    <ul v-if="notes.length" class="space-y-1.5">
      <li
        v-for="note in sorted"
        :key="note.id"
        class="rounded border border-border bg-panel-2/40 p-2"
      >
        <div class="flex items-start gap-2">
          <span class="mt-1 h-1.5 w-1.5 shrink-0 rounded-full" :class="DOT[note.severity]"></span>
          <div class="min-w-0 flex-1">
            <p class="text-xs font-medium text-text">{{ note.title }}</p>
            <p class="mt-0.5 text-[11px] leading-snug text-text-dim">{{ note.detail }}</p>
            <!-- The number it heard. Always shown, so a note can be checked
                 instead of believed. -->
            <p v-if="note.evidence" class="mt-0.5 font-mono text-[10px] text-text-faint">
              {{ note.evidence }}
            </p>
            <button
              v-if="note.action"
              type="button"
              class="mt-1.5 rounded border px-2 py-1 text-[11px] transition-colors"
              :class="applied.has(note.id)
                ? 'border-accent1/40 text-accent1'
                : 'border-accent2/60 bg-accent2/10 text-text hover:border-accent2'"
              :disabled="applied.has(note.id)"
              @click="apply(note)"
            >{{ applied.has(note.id) ? t('coproducer.done') : note.action.label }}</button>
          </div>
        </div>
      </li>
    </ul>

    <p v-if="error" class="rounded bg-status-failed/10 p-2 text-[11px] text-status-failed">
      {{ error }}
    </p>
  </div>
</template>
