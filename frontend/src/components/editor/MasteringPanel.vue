<script setup lang="ts">
/**
 * Automatic mastering.
 *
 * Everything on screen here is a measurement, because the thing that makes
 * an automatic master trustworthy is not that it sounds good in the demo -
 * it is that you can see what it did and check it against a delivery spec.
 * LUFS and true peak are the two numbers a streaming service actually
 * applies, so they are the two shown largest.
 *
 * The reference mode is the one worth reaching for. A genre preset is an
 * average of records that do not sound like each other; a reference is one
 * record you already decided you liked.
 */
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiFetch, apiJson } from '../../api/http'
import * as tracksApi from '../../api/tracks'

interface Band { hz: number; db: number }
interface Analysis {
  lufs: number
  shortTermMaxLufs: number
  lra: number
  truePeakDb: number
  samplePeakDb: number
  crestDb: number
  correlation: number
  seconds: number
  bands: Band[]
}
interface Plan {
  highPassHz: number
  compressorRatio: number
  widthScale: number
  preGainDb: number
  ceilingDb: number
  gainReductionDb: number
  loudnessMissLu: number
  eq: Band[]
}
interface MasterResult {
  before: Analysis
  after: Analysis
  plan: Plan
  target: string
  targetLufs: number
  title: string
  track_id: number
}

const { t } = useI18n()

const tracks = ref<{ id: number; title: string; audio_url: string }[]>([])
const trackId = ref<number | null>(null)
const referenceId = ref<number | null>(null)
const target = ref('streaming')
const targets = ref<{ name: string; lufs: number }[]>([])
const analysis = ref<Analysis | null>(null)
const result = ref<MasterResult | null>(null)
const busy = ref(false)
const error = ref('')

const wavOnly = computed(() => tracks.value)

// Whether the result will survive a streaming service's own normalisation
// without being turned down or clipped. This is the whole delivery question
// in one line.
function verdict(a: Analysis): { text: string; ok: boolean } {
  if (a.truePeakDb > -0.9) return { text: t('mastering.willClip'), ok: false }
  if (a.lufs > -8.5) return { text: t('mastering.willBeTurnedDown'), ok: false }
  return { text: t('mastering.deliveryOk'), ok: true }
}

/**
 * Fills the two pickers.
 *
 * Settled, not all. These come from unrelated places - the track list from
 * the library, the presets from the native engine - and Promise.all threw
 * the list away whenever the engine was down, so a panel that could still
 * have shown you every track in your library showed two empty dropdowns
 * and one error. The engine being unavailable is a normal state (it is not
 * started until something needs it), so it must not be able to blank the
 * half of the panel that does not depend on it.
 */
async function load() {
  const [list, presets] = await Promise.allSettled([
    tracksApi.listTracks(),
    apiFetch<{ targets: { name: string; lufs: number }[] }>('/api/engine/mastering/targets'),
  ])

  if (list.status === 'fulfilled') {
    const value = list.value
    tracks.value = (Array.isArray(value) ? value : (value as { data?: [] }).data ?? []) as never
    if (!trackId.value && tracks.value.length) trackId.value = tracks.value[0].id
  } else {
    error.value = list.reason instanceof Error ? list.reason.message : String(list.reason)
  }

  if (presets.status === 'fulfilled') {
    targets.value = presets.value.targets
  } else {
    // Named, so the message points at the engine rather than at mastering
    // in general - the fix is to start the engine, and the user cannot
    // guess that from "failed to fetch".
    const why = presets.reason instanceof Error ? presets.reason.message : String(presets.reason)
    error.value = t('mastering.targetsUnavailable', { why })
  }
}

async function run(action: () => Promise<unknown>) {
  busy.value = true
  error.value = ''
  try {
    await action()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function analyse() {
  result.value = null
  await run(async () => {
    const r = await apiJson<{ analysis: Analysis }>('/api/engine/mastering/analyze', {
      track_id: trackId.value,
    })
    analysis.value = r.analysis
  })
}

async function master() {
  await run(async () => {
    result.value = await apiJson<MasterResult>('/api/engine/mastering/master', {
      track_id: trackId.value,
      target: target.value,
      reference_track_id: referenceId.value || undefined,
    })
    analysis.value = result.value.before
    await load()
  })
}

const db = (value: number | undefined) => (value === undefined ? '-' : value.toFixed(2))

onMounted(load)
</script>

<template>
  <div class="space-y-3">
    <p class="text-xs text-text-dim">{{ t('mastering.intro') }}</p>

    <!-- What to master -->
    <div class="grid gap-2 sm:grid-cols-2">
      <label class="space-y-1">
        <span class="text-[10px] uppercase tracking-wider text-text-faint">{{ t('mastering.track') }}</span>
        <select
          v-model.number="trackId"
          class="w-full rounded border border-border bg-panel-2 px-2 py-1.5 text-xs text-text"
        >
          <option v-for="item in wavOnly" :key="item.id" :value="item.id">{{ item.title }}</option>
        </select>
      </label>

      <label class="space-y-1">
        <span class="text-[10px] uppercase tracking-wider text-text-faint">{{ t('mastering.target') }}</span>
        <select
          v-model="target"
          :disabled="!!referenceId"
          class="w-full rounded border border-border bg-panel-2 px-2 py-1.5 text-xs text-text disabled:opacity-40"
        >
          <option v-for="preset in targets" :key="preset.name" :value="preset.name">
            {{ preset.name }} ({{ preset.lufs }} LUFS)
          </option>
        </select>
      </label>
    </div>

    <label class="space-y-1 block">
      <span class="text-[10px] uppercase tracking-wider text-text-faint">
        {{ t('mastering.reference') }}
      </span>
      <select
        v-model.number="referenceId"
        class="w-full rounded border border-border bg-panel-2 px-2 py-1.5 text-xs text-text"
      >
        <option :value="null">{{ t('mastering.noReference') }}</option>
        <option v-for="item in wavOnly" :key="item.id" :value="item.id">{{ item.title }}</option>
      </select>
      <span class="block text-[10px] text-text-faint">{{ t('mastering.referenceHint') }}</span>
    </label>

    <div class="flex flex-wrap gap-1.5">
      <button
        type="button"
        class="rounded border border-border px-3 py-1.5 text-xs text-text-dim hover:text-text disabled:opacity-40"
        :disabled="busy || !trackId"
        @click="analyse"
      >{{ t('mastering.analyse') }}</button>
      <button
        type="button"
        class="accent-gradient rounded px-3 py-1.5 text-xs disabled:opacity-40"
        :disabled="busy || !trackId"
        @click="master"
      >{{ busy ? t('mastering.working') : t('mastering.master') }}</button>
    </div>

    <!-- Measurements -->
    <div v-if="analysis" class="rounded border border-border bg-panel-2/40 p-2">
      <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div>
          <p class="text-[9px] uppercase tracking-wider text-text-faint">{{ t('mastering.loudness') }}</p>
          <p class="font-mono text-sm text-text">{{ db(analysis.lufs) }}<span class="text-[10px] text-text-faint"> LUFS</span></p>
        </div>
        <div>
          <p class="text-[9px] uppercase tracking-wider text-text-faint">{{ t('mastering.truePeak') }}</p>
          <p class="font-mono text-sm" :class="analysis.truePeakDb > -0.9 ? 'text-status-failed' : 'text-text'">
            {{ db(analysis.truePeakDb) }}<span class="text-[10px] text-text-faint"> dBTP</span>
          </p>
        </div>
        <div>
          <p class="text-[9px] uppercase tracking-wider text-text-faint">{{ t('mastering.range') }}</p>
          <p class="font-mono text-sm text-text">{{ db(analysis.lra) }}<span class="text-[10px] text-text-faint"> LU</span></p>
        </div>
        <div>
          <p class="text-[9px] uppercase tracking-wider text-text-faint">{{ t('mastering.correlation') }}</p>
          <p class="font-mono text-sm" :class="analysis.correlation < 0.2 ? 'text-status-queued' : 'text-text'">
            {{ analysis.correlation.toFixed(2) }}
          </p>
        </div>
      </div>

      <!-- Tonal balance, drawn rather than tabulated: a spectrum is a shape. -->
      <div class="mt-2 flex h-14 items-end gap-0.5">
        <div
          v-for="band in analysis.bands"
          :key="band.hz"
          class="flex-1 rounded-t bg-accent1/60"
          :style="{ height: Math.max(2, Math.min(100, (band.db + 100) * 1.4)) + '%' }"
          :title="`${band.hz < 1000 ? band.hz.toFixed(0) + ' Hz' : (band.hz / 1000).toFixed(1) + ' kHz'}: ${band.db.toFixed(1)} dB`"
        ></div>
      </div>
      <div class="flex justify-between text-[9px] text-text-faint">
        <span>40 Hz</span><span>1 kHz</span><span>16 kHz</span>
      </div>
    </div>

    <!-- What it did -->
    <div v-if="result" class="space-y-2 rounded border border-accent1/40 bg-accent1/5 p-2">
      <p class="text-[11px] font-medium text-text">
        {{ t('mastering.done', { title: result.title }) }}
      </p>

      <div class="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
        <span class="text-text-faint">{{ t('mastering.loudness') }}</span>
        <span class="font-mono text-text">
          {{ db(result.before.lufs) }} → {{ db(result.after.lufs) }} LUFS
        </span>
        <span class="text-text-faint">{{ t('mastering.truePeak') }}</span>
        <span class="font-mono text-text">
          {{ db(result.before.truePeakDb) }} → {{ db(result.after.truePeakDb) }} dBTP
        </span>
        <span class="text-text-faint">{{ t('mastering.range') }}</span>
        <span class="font-mono text-text">
          {{ db(result.before.lra) }} → {{ db(result.after.lra) }} LU
        </span>
        <span class="text-text-faint">{{ t('mastering.limiting') }}</span>
        <span class="font-mono text-text">{{ db(result.plan.gainReductionDb) }} dB</span>
      </div>

      <p
        class="rounded px-2 py-1 text-[11px]"
        :class="verdict(result.after).ok
          ? 'bg-accent1/10 text-accent1'
          : 'bg-status-queued/10 text-status-queued'"
      >{{ verdict(result.after).text }}</p>

      <!-- Said plainly when the limiter ran out of room, rather than
           delivering something quieter than asked and saying nothing. -->
      <p v-if="Math.abs(result.plan.loudnessMissLu) > 0.5" class="text-[10px] leading-snug text-text-dim">
        {{ t('mastering.missed', {
          target: result.targetLufs.toFixed(1),
          got: result.after.lufs.toFixed(1),
        }) }}
      </p>

      <details class="text-[10px] text-text-dim">
        <summary class="cursor-pointer text-text-faint">{{ t('mastering.chain') }}</summary>
        <ul class="mt-1 space-y-0.5">
          <li v-if="result.plan.highPassHz > 0">
            {{ t('mastering.highPass', { hz: result.plan.highPassHz.toFixed(0) }) }}
          </li>
          <li v-for="move in result.plan.eq" :key="move.hz">
            {{ move.hz < 1000 ? move.hz.toFixed(0) + ' Hz' : (move.hz / 1000).toFixed(1) + ' kHz' }}
            {{ move.db > 0 ? '+' : '' }}{{ move.db.toFixed(1) }} dB
          </li>
          <li v-if="result.plan.compressorRatio > 1">
            {{ t('mastering.glue', { ratio: result.plan.compressorRatio.toFixed(1) }) }}
          </li>
          <li v-if="result.plan.widthScale !== 1">
            {{ t('mastering.width', { percent: Math.round(result.plan.widthScale * 100) }) }}
          </li>
          <li>{{ t('mastering.limiter', { ceiling: result.plan.ceilingDb.toFixed(1) }) }}</li>
        </ul>
      </details>
    </div>

    <p v-if="error" class="rounded bg-status-failed/10 p-2 text-[11px] text-status-failed">{{ error }}</p>
  </div>
</template>
