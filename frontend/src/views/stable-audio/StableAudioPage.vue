<script setup lang="ts">
/**
 * Stable Audio 3 - the beat engine.
 *
 * Its own tab rather than another mode inside the generate page, because it
 * answers a different question. ACE-Step and YuE2 are asked for a *song*;
 * this is asked for an instrumental, fast, with the tempo and key you named.
 * It leads every open model on tempo and key adherence and runs in eight
 * diffusion steps where ACE-Step takes sixty.
 *
 * It does not sing, and that is not a gap to apologise for - vocals come from
 * a take recorded on the timeline and re-voiced with Seed-VC, or from YuE2's
 * vocal separated out of a full generation. The tab says so rather than
 * letting someone discover it by typing lyrics and getting humming.
 */
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import PageFrame from '../../components/shared/PageFrame.vue'
import * as sa from '../../api/stableAudio'
import * as yue2Api from '../../api/yue2'
import * as tracksApi from '../../api/tracks'
import { useOrchestratorStore } from '../../stores/orchestrator'
import ModelOfflineBanner from '../../components/shared/ModelOfflineBanner.vue'

const { t } = useI18n()
const orchestrator = useOrchestratorStore()

const engineRunning = computed(() => orchestrator.statuses?.yue2?.status === 'running')

const prompt = ref('')
const negativePrompt = ref('')
const duration = ref(45)
const steps = ref(8)
const guidance = ref(1.0)
const sampler = ref<sa.Sampler>('pingpong')
const seed = ref<number | null>(null)
const memSaver = ref(true)

const busy = ref(false)
const error = ref('')
const installed = ref<boolean | null>(null)

interface Take {
  url: string
  title: string
  seconds: number | null
  wallSec: number | null
  savedId: number | null
}
const takes = ref<Take[]>([])

async function checkInstalled() {
  if (!engineRunning.value) return
  installed.value = await sa.isInstalled()
}

async function generate() {
  if (!prompt.value.trim() || busy.value) return
  busy.value = true
  error.value = ''
  const startedAt = performance.now()
  try {
    const result = await sa.generate({
      prompt: prompt.value.trim(),
      negativePrompt: negativePrompt.value,
      durationSeconds: duration.value,
      steps: steps.value,
      guidanceScale: guidance.value,
      sampler: sampler.value,
      seed: seed.value,
      memSaver: memSaver.value,
    })
    if (typeof result.audio !== 'string') throw new Error(t('stableAudio.noAudio'))

    const blob = yue2Api.base64AudioBlob(result.audio)
    const wallMs = result.timing?.wall_ms ?? performance.now() - startedAt
    const durationMs = result.timing?.audio_duration_ms ?? null

    const take: Take = {
      url: URL.createObjectURL(blob),
      title: prompt.value.trim().slice(0, 60),
      seconds: durationMs ? durationMs / 1000 : duration.value,
      wallSec: wallMs / 1000,
      savedId: null,
    }
    takes.value.unshift(take)

    // Straight into the library, so it is available to the DAW, the LoRA
    // dataset builder and everything else without a manual export step.
    try {
      const saved = await tracksApi.saveTrack(
        {
          model: 'stable_audio',
          title: take.title,
          lyrics: '',
          seed: seed.value ?? undefined,
          duration_ms: durationMs ?? undefined,
          wall_ms: wallMs,
          params: {
            prompt: prompt.value.trim(),
            negative_prompt: negativePrompt.value,
            steps: steps.value,
            guidance_scale: guidance.value,
            sampler: sampler.value,
          },
        },
        blob,
        'wav',
        null,
      )
      take.savedId = saved.id
    } catch {
      // The take is still playable above; only the library copy failed.
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

onMounted(checkInstalled)
</script>

<template>
  <PageFrame :title="t('stableAudio.title')" :subtitle="t('stableAudio.subtitle')">

    <ModelOfflineBanner
      v-if="!engineRunning"
      model-id="yue2"
      :status="orchestrator.statuses?.yue2?.status"
      :error="orchestrator.statuses?.yue2?.error ?? null"
    />

    <section class="rounded-xl border border-border bg-panel p-5">
      <p class="mt-1 text-xs text-text-dim">{{ t('stableAudio.instrumentalNote') }}</p>

      <p
        v-if="engineRunning && installed === false"
        class="mt-3 rounded-lg border border-status-queued/40 bg-status-queued/10 p-3 text-xs text-text-dim"
      >
        {{ t('stableAudio.notInstalled') }}
        <code class="mt-1 block select-all rounded bg-panel-2 px-2 py-1 text-[11px] text-text">python tools/model_manager_v2.py install stable_audio_3_medium_q8_0 --models-root models --progress</code>
      </p>

      <label class="mt-4 block text-sm text-text-dim">
        {{ t('stableAudio.prompt') }}
        <textarea
          v-model="prompt"
          rows="2"
          :placeholder="t('stableAudio.promptPlaceholder')"
          class="mt-1 w-full rounded-lg border border-border bg-panel-2 p-2.5 text-sm text-text"
        ></textarea>
      </label>

      <label class="mt-3 block text-xs text-text-dim">
        {{ t('stableAudio.negative') }}
        <input
          v-model="negativePrompt"
          type="text"
          :placeholder="t('stableAudio.negativePlaceholder')"
          class="mt-1 w-full rounded-lg border border-border bg-panel-2 px-2 py-1.5 text-xs text-text"
        />
      </label>

      <div class="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label class="text-xs text-text-dim">
          {{ t('stableAudio.duration', { n: duration }) }}
          <input v-model.number="duration" type="range" min="5" max="300" step="5" class="mt-1 w-full accent-accent1" />
        </label>
        <label class="text-xs text-text-dim">
          {{ t('stableAudio.steps', { n: steps }) }}
          <input v-model.number="steps" type="range" min="4" max="64" step="1" class="mt-1 w-full accent-accent1" />
        </label>
        <label class="text-xs text-text-dim">
          {{ t('stableAudio.guidance', { n: guidance.toFixed(1) }) }}
          <input v-model.number="guidance" type="range" min="0.5" max="12" step="0.1" class="mt-1 w-full accent-accent1" />
        </label>
        <label class="text-xs text-text-dim">
          {{ t('stableAudio.sampler') }}
          <select v-model="sampler" class="mt-1 w-full rounded border border-border bg-panel-2 px-2 py-1.5 text-xs text-text">
            <option v-for="option in sa.SAMPLERS" :key="option" :value="option">{{ option }}</option>
          </select>
        </label>
      </div>

      <div class="mt-3 flex flex-wrap items-center gap-3">
        <label class="text-xs text-text-dim">
          {{ t('stableAudio.seed') }}
          <input
            v-model.number="seed"
            type="number"
            :placeholder="t('stableAudio.seedRandom')"
            class="ml-1 w-32 rounded border border-border bg-panel-2 px-2 py-1 text-xs text-text"
          />
        </label>
        <label class="flex items-center gap-1.5 text-xs text-text-dim">
          <input v-model="memSaver" type="checkbox" class="accent-accent1" />
          {{ t('stableAudio.memSaver') }}
        </label>
        <button
          type="button"
          class="accent-gradient ml-auto rounded-lg px-5 py-2 text-sm font-medium text-white disabled:opacity-40"
          :disabled="!engineRunning || busy || !prompt.trim()"
          @click="generate"
        >
          {{ busy ? t('stableAudio.generating') : t('stableAudio.generate') }}
        </button>
      </div>
      <p class="mt-2 text-[11px] text-text-dim">{{ t('stableAudio.memSaverNote') }}</p>

      <p v-if="error" class="mt-3 whitespace-pre-line rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">
        {{ error }}
      </p>
    </section>

    <section v-if="takes.length" class="space-y-2">
      <h3 class="text-sm font-semibold text-text">{{ t('stableAudio.takes') }}</h3>
      <div v-for="(take, index) in takes" :key="index" class="rounded-xl border border-border bg-panel p-3">
        <div class="flex flex-wrap items-baseline gap-2">
          <p class="min-w-0 flex-1 truncate text-sm text-text">{{ take.title }}</p>
          <span class="text-[11px] text-text-dim">
            {{ take.seconds?.toFixed(0) }}s · {{ t('stableAudio.took', { n: take.wallSec?.toFixed(1) }) }}
          </span>
          <span v-if="take.savedId" class="text-[11px] text-status-done">{{ t('stableAudio.saved') }}</span>
        </div>
        <audio :src="take.url" controls class="mt-2 w-full"></audio>
      </div>
    </section>
  </PageFrame>
</template>
