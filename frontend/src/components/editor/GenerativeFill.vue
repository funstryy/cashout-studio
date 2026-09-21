<script setup lang="ts">
/**
 * Generative fill, driven from the arrangement.
 *
 * The span comes from the loop region rather than a selection tool of its
 * own: a DAW already has a way to say "these bars", people already know how
 * to set it, and inventing a second range control that meant almost the same
 * thing would be worse than reusing the first.
 *
 * Two buttons, two questions. "Regenerate" replaces a span of one track with
 * something that matches what surrounds it. "Add part" hands the model a
 * bounce of everything playing and asks for a bass line - or keys, or
 * percussion - that sits with it, which arrives as its own lane.
 */
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import * as fill from '../../audio/generativeFill'
import type { TimelineLane } from '../../audio/timelineTypes'

const props = defineProps<{
  lanes: TimelineLane[]
  buffers: Map<string, AudioBuffer>
  loopStart: number
  loopEnd: number
  loopEnabled: boolean
  totalDuration: number
  engineReady: boolean
}>()

const emit = defineEmits<{
  /** A lane's audio was regenerated; the page uploads it and reseats the clips. */
  replaceLane: [payload: { laneIndex: number; buffer: AudioBuffer; label: string }]
  /** A new part was written; the page gives it a lane. */
  newPart: [payload: { buffer: AudioBuffer; name: string }]
  /** Ask the page to set the loop region, so the range control stays one thing. */
  setRange: [payload: { startSec: number; endSec: number }]
}>()

const { t } = useI18n()

const PARTS = ['bass', 'drums', 'keys', 'piano', 'guitar', 'strings', 'synth', 'percussion', 'vocals'] as const

const laneIndex = ref(0)
const prompt = ref('')
const part = ref<string>('bass')
const busy = ref<'' | 'fill' | 'part'>('')
const stage = ref('')
const progress = ref(0)
const error = ref('')

const hasRange = computed(() => props.loopEnabled && props.loopEnd > props.loopStart)
const rangeLabel = computed(() =>
  hasRange.value ? `${props.loopStart.toFixed(1)}s – ${props.loopEnd.toFixed(1)}s` : t('genFill.noRange'),
)

const lane = computed(() => props.lanes[laneIndex.value] ?? null)

/** One buffer for the whole lane, since the engine wants the track, not a clip. */
function laneBuffer(): AudioBuffer | null {
  const current = lane.value
  if (!current?.clips.length) return null
  const first = props.buffers.get(current.clips[0].sourceUrl)
  if (!first) return null
  return fill.bounceArrangement(
    props.buffers,
    [{ clips: current.clips, settings: { muted: false, volume: 1 } }],
    props.totalDuration,
    first.sampleRate,
  )
}

function onProgress(nextStage: string, fraction: number) {
  stage.value = nextStage
  progress.value = Math.round(Math.max(0, Math.min(100, fraction)))
}

async function regenerate() {
  const current = lane.value
  if (!current || !hasRange.value || busy.value) return
  const source = laneBuffer()
  if (!source) {
    error.value = t('genFill.emptyLane')
    return
  }

  busy.value = 'fill'
  error.value = ''
  stage.value = ''
  progress.value = 0
  try {
    const generated = await fill.fillRegion({
      source,
      startSec: props.loopStart,
      endSec: Math.min(props.loopEnd, source.duration),
      prompt: prompt.value.trim(),
      onProgress,
    })
    // Spliced rather than swapped wholesale: the edit must not be able to
    // move anything outside its own span, whatever length the engine returns.
    const merged = fill.spliceRegion(source, generated, props.loopStart,
                                     Math.min(props.loopEnd, source.duration))
    emit('replaceLane', { laneIndex: laneIndex.value, buffer: merged, label: current.name })
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = ''
  }
}

async function generatePart() {
  if (busy.value) return
  const first = props.lanes.flatMap((l) => l.clips).map((c) => props.buffers.get(c.sourceUrl)).find(Boolean)
  if (!first) {
    error.value = t('genFill.emptyProject')
    return
  }
  const arrangement = fill.bounceArrangement(props.buffers, props.lanes, props.totalDuration, first.sampleRate)
  if (!arrangement) {
    error.value = t('genFill.emptyProject')
    return
  }

  busy.value = 'part'
  error.value = ''
  stage.value = ''
  progress.value = 0
  try {
    const generated = await fill.addPart({
      arrangement,
      trackName: part.value,
      prompt: prompt.value.trim(),
      onProgress,
    })
    emit('newPart', { buffer: generated, name: part.value })
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = ''
  }
}
</script>

<template>
  <section>
    <p class="text-xs text-text-dim">{{ t('genFill.subtitle') }}</p>

    <p v-if="!engineReady" class="mt-2 rounded-lg border border-status-queued/40 bg-status-queued/10 p-2 text-xs text-text-dim">
      {{ t('genFill.engineOffline') }}
    </p>

    <div class="mt-3 grid gap-2 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
      <label class="text-xs text-text-dim">
        {{ t('genFill.track') }}
        <select
          v-model.number="laneIndex"
          class="mt-1 w-full rounded border border-border bg-panel-2 px-2 py-1.5 text-xs text-text"
        >
          <option v-for="(l, index) in lanes" :key="l.id" :value="index">{{ l.name }}</option>
        </select>
      </label>

      <label class="text-xs text-text-dim">
        {{ t('genFill.prompt') }}
        <input
          v-model="prompt"
          type="text"
          :placeholder="t('genFill.promptPlaceholder')"
          class="mt-1 w-full rounded border border-border bg-panel-2 px-2 py-1.5 text-xs text-text"
        />
      </label>
    </div>

    <div class="mt-3 flex flex-wrap items-center gap-2">
      <span
        class="rounded px-2 py-1 text-[11px]"
        :class="hasRange ? 'bg-accent1/15 text-text' : 'bg-panel-2 text-text-dim'"
      >
        {{ t('genFill.range') }}: {{ rangeLabel }}
      </span>
      <button
        type="button"
        class="accent-gradient rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
        :disabled="!hasRange || !engineReady || !!busy"
        @click="regenerate"
      >
        {{ busy === 'fill' ? t('genFill.working') : t('genFill.regenerate') }}
      </button>

      <span class="mx-1 h-5 w-px bg-border"></span>

      <select
        v-model="part"
        class="rounded border border-border bg-panel-2 px-2 py-1.5 text-xs text-text"
      >
        <option v-for="option in PARTS" :key="option" :value="option">{{ option }}</option>
      </select>
      <button
        type="button"
        class="rounded-lg border border-accent1/60 bg-accent1/10 px-4 py-2 text-sm text-text disabled:opacity-40"
        :disabled="!engineReady || !!busy"
        @click="generatePart"
      >
        {{ busy === 'part' ? t('genFill.working') : t('genFill.addPart') }}
      </button>
    </div>

    <p class="mt-2 text-[11px] text-text-dim">
      {{ hasRange ? t('genFill.hintReady') : t('genFill.hintSetLoop') }}
    </p>

    <div v-if="busy" class="mt-3 space-y-1">
      <div class="h-1.5 overflow-hidden rounded-full bg-panel-2">
        <div class="accent-gradient h-full transition-all" :style="{ width: `${progress}%` }"></div>
      </div>
      <p class="text-[11px] text-text-dim">{{ stage || t('genFill.working') }} · {{ progress }}%</p>
    </div>

    <p v-if="error" class="mt-2 whitespace-pre-line rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">
      {{ error }}
    </p>
  </section>
</template>
