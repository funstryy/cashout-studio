<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import PageFrame from '../../components/shared/PageFrame.vue'
import { useOrchestratorStore } from '../../stores/orchestrator'
import { useAceStepStore } from '../../stores/aceStep'
import * as trainingApi from '../../api/aceStepTraining'
import ModelOfflineBanner from '../../components/shared/ModelOfflineBanner.vue'
import GenerateForm from './GenerateForm.vue'
import ResultsFeed from './ResultsFeed.vue'

const orchestrator = useOrchestratorStore()
const store = useAceStepStore()
const { t } = useI18n()

const modelStatus = computed(() => orchestrator.statuses.ace_step?.status ?? 'stopped')
const modelError = computed(() => orchestrator.statuses.ace_step?.error ?? null)
const isRunning = computed(() => modelStatus.value === 'running')

watch(
  isRunning,
  (running) => {
    if (running) void store.loadInventory()
  },
  { immediate: true },
)

onMounted(() => {
  void store.loadHistory()
  store.startBackgroundTasks()
})
onBeforeUnmount(() => store.stopBackgroundTasks())

// Training and generation share the same GPU/model process, so while a LoRA
// training run is active (started from the /ace-step/lora tab, possibly in
// another browser tab) the generation form is replaced with a notice instead
// of letting the user submit jobs that would just fail server-side.
const isTraining = ref(false)
let trainingPollTimer: ReturnType<typeof setInterval> | null = null
async function pollTraining() {
  if (!isRunning.value) return
  try {
    const status = await trainingApi.trainingStatus()
    isTraining.value = status.is_training
  } catch {
    // Leave last known value - a transient failure shouldn't flip the banner.
  }
}
onMounted(() => {
  void pollTraining()
  trainingPollTimer = setInterval(pollTraining, 10000)
})
onUnmounted(() => {
  if (trainingPollTimer) clearInterval(trainingPollTimer)
})
</script>

<template>
  <PageFrame :title="t('nav.generate')" accent="var(--color-accent2)">

    <ModelOfflineBanner v-if="!isRunning" model-id="ace_step" :status="modelStatus" :error="modelError" />
    <div v-else-if="isTraining" class="rounded-xl border border-status-queued/40 bg-status-queued/10 p-6 text-center text-sm text-text-dim">
      {{ t('acePage.trainingActive') }}
      <RouterLink to="/ace-step/lora" class="text-accent1 hover:underline">{{ t('acePage.openTrainingPage') }}</RouterLink>
    </div>
    <div class="grid grid-cols-1 gap-6 lg:grid-cols-[480px_minmax(0,1fr)]">
      <GenerateForm v-if="isRunning && !isTraining" />
      <ResultsFeed :class="{ 'lg:col-span-2': !isRunning || isTraining }" />
    </div>
  </PageFrame>
</template>
