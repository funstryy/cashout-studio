<script setup lang="ts">
/**
 * The first four launches.
 *
 * One short tutorial per launch rather than one long one on the first,
 * because a tour of a program you have never opened is a list of words you
 * cannot attach to anything. Spreading it over four sessions means each
 * part arrives when the previous one has had a chance to become familiar:
 * launch one is the map, two is making a track, three is the parts that
 * make it a DAW, four is finishing and the AI.
 *
 * It is a panel, not a coach-mark system pointing at live elements. Arrows
 * anchored to real controls look better in a screenshot and break the
 * moment a panel is closed, a rail is hidden at a narrow width, or the
 * dock is showing the other tab - and every one of those is a normal state
 * here. A panel that describes where to look cannot point at the wrong
 * thing.
 *
 * Skipping one launch's tutorial does not skip the rest; "Don't show
 * tutorials" does. The counter only advances when a launch actually shows
 * one, so closing the app four times without reading anything does not
 * quietly use them up.
 */
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

const { t, tm, rt } = useI18n()

const SEEN_KEY = 'cashout_tour_runs_seen'
const OFF_KEY = 'cashout_tour_disabled'

type Step = { name: string; body: string }
type Run = { title: string; blurb: string; steps: Step[] }

const runs = computed<Run[]>(() =>
  (tm('tour.runs') as unknown[]).map((raw) => {
    const run = raw as { title: unknown; blurb: unknown; steps: unknown[] }
    return {
      title: rt(run.title as never),
      blurb: rt(run.blurb as never),
      steps: run.steps.map((entry) => {
        const step = entry as { name: unknown; body: unknown }
        return { name: rt(step.name as never), body: rt(step.body as never) }
      }),
    }
  }),
)

/** Which run this launch should show, or null for none. */
const runIndex = ref<number | null>(null)
const stepIndex = ref(0)

const run = computed<Run | null>(() =>
  runIndex.value === null ? null : runs.value[runIndex.value] ?? null,
)
const step = computed<Step | null>(() => run.value?.steps[stepIndex.value] ?? null)
const isLast = computed(() => !!run.value && stepIndex.value >= run.value.steps.length - 1)

function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    // Private mode, or storage blocked. Treated as a fresh install, which
    // shows the first tutorial again - annoying, not broken.
    return null
  }
}

function write(key: string, value: string) {
  try {
    localStorage.setItem(key, value)
  } catch {
    // Nothing to do. The tour simply will not be remembered.
  }
}

function close() {
  runIndex.value = null
}

function neverAgain() {
  write(OFF_KEY, '1')
  close()
}

function next() {
  if (!run.value) return
  if (isLast.value) {
    close()
    return
  }
  stepIndex.value += 1
}

onMounted(() => {
  if (read(OFF_KEY) === '1') return
  const seen = Number(read(SEEN_KEY))
  const done = Number.isFinite(seen) && seen > 0 ? seen : 0
  if (done >= runs.value.length) return
  runIndex.value = done
  // Counted as used the moment it is shown. Marking it on completion would
  // repeat the same tutorial forever for anyone who skips - which is the
  // one group who has already told you what they want.
  write(SEEN_KEY, String(done + 1))
})
</script>

<template>
  <div
    v-if="run && step"
    class="fixed inset-0 z-[150] flex items-center justify-center bg-black/70 p-4"
  >
    <div class="card w-full max-w-lg shadow-2xl">
      <header class="rack-strip flex items-center gap-2 px-3 py-2">
        <span
          class="led h-[6px] w-[6px]"
          style="background: var(--color-accent1); color: var(--color-accent1)"
        ></span>
        <h2 class="engraved text-[11px] font-semibold uppercase text-accent1">
          {{ run.title }}
        </h2>
        <span class="readout-dim ml-auto text-[10px]">
          {{ t('tour.runLabel', { n: (runIndex ?? 0) + 1 }) }}
        </span>
      </header>

      <div class="space-y-3 p-4">
        <p class="text-[11px] leading-relaxed text-text-faint">{{ run.blurb }}</p>

        <div class="inset p-3">
          <p class="text-[13px] font-medium text-text">{{ step.name }}</p>
          <p class="mt-1.5 text-[12px] leading-relaxed text-text-dim">{{ step.body }}</p>
        </div>

        <!-- Progress as lamps rather than a bar: four of them is a count
             you read at a glance, and a bar 25% full says less. -->
        <div class="flex items-center gap-2">
          <span
            v-for="(_, i) in run.steps"
            :key="i"
            class="led h-[5px] w-[5px]"
            :style="{
              background: i <= stepIndex ? 'var(--color-accent1)' : '#10161d',
              color: i <= stepIndex ? 'var(--color-accent1)' : 'transparent',
            }"
          ></span>
          <span class="readout-dim ml-1 text-[10px]">
            {{ t('tour.progress', { n: stepIndex + 1, total: run.steps.length }) }}
          </span>
        </div>
      </div>

      <footer class="divider-engraved flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          class="text-[10px] text-text-faint transition-colors hover:text-text"
          @click="neverAgain"
        >{{ t('tour.never') }}</button>

        <div class="ml-auto flex items-center gap-1.5">
          <button type="button" class="key px-2.5 py-1 text-[11px]" @click="close">
            {{ t('tour.skip') }}
          </button>
          <button
            v-if="stepIndex > 0"
            type="button"
            class="key px-2.5 py-1 text-[11px]"
            @click="stepIndex -= 1"
          >{{ t('tour.back') }}</button>
          <button
            type="button"
            class="accent-gradient px-3 py-1 text-[11px]"
            @click="next"
          >{{ isLast ? t('tour.done') : t('tour.next') }}</button>
        </div>
      </footer>
    </div>
  </div>
</template>
