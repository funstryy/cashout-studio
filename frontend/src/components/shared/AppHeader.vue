<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { useOrchestratorStore } from '../../stores/orchestrator'
import { MODEL_LABELS, MODEL_ROUTES, useModelSwitch } from '../../composables/useModelSwitch'
import { setLocale, currentLocale, type LocaleCode } from '../../i18n'
import type { ModelId, ModelRuntimeStatus } from '../../types'

const orchestrator = useOrchestratorStore()
const route = useRoute()
const { selectModel } = useModelSwitch()
const { t } = useI18n()

const LOCALES: { code: LocaleCode; label: string }[] = [
  { code: 'ru', label: 'Русский' },
  { code: 'en', label: 'English' },
]

function onLocaleChange(e: Event) {
  setLocale((e.target as HTMLSelectElement).value as LocaleCode)
}

const MODEL_IDS: ModelId[] = ['ace_step', 'yue2']

function statusOf(id: ModelId): ModelRuntimeStatus {
  return orchestrator.statuses[id]?.status ?? 'stopped'
}

const LED_CLASSES: Record<ModelRuntimeStatus, string> = {
  stopped: 'bg-gray-500',
  starting: 'bg-status-queued animate-pulse',
  running: 'bg-status-done',
  stopping: 'bg-status-queued animate-pulse',
  error: 'bg-status-failed',
}

const STATUS_LABEL_KEYS: Record<ModelRuntimeStatus, string> = {
  stopped: 'modelStatus.stopped',
  starting: 'modelStatus.starting',
  running: 'modelStatus.running',
  stopping: 'modelStatus.stopping',
  error: 'modelStatus.error',
}

async function onSelect(id: ModelId) {
  try {
    await selectModel(id)
  } catch {
    // orchestrator.switchError already holds the message, rendered below.
  }
}
</script>

<template>
  <header class="sticky top-0 z-40 border-b border-border bg-bg/90 backdrop-blur">
    <div class="mx-auto flex w-full max-w-7xl flex-wrap items-center gap-4 px-4 py-3 sm:px-6">
      <router-link to="/" class="flex items-center gap-2 text-text">
        <img src="/cashout-studio-logo.svg" alt="" class="h-7 w-7 shrink-0 rounded-md" />
        <span class="flex flex-col leading-tight">
          <span class="text-lg font-semibold">Cashout Studio</span>
          <span class="text-[10px] text-text-dim">{{ t('header.tagline') }}</span>
        </span>
      </router-link>

      <nav class="ml-auto flex flex-wrap gap-2">
        <router-link
          to="/editor"
          class="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors"
          :class="route.path.startsWith('/editor') ? 'border-accent1/60 bg-panel text-text' : 'border-border bg-panel-2 text-text-dim hover:text-text'"
        >
          {{ t('header.editor') }}
        </router-link>
        <!-- Both of these are shown even when their engine is stopped: each
             page renders an offline banner with a start button, and hiding
             the tabs entirely made two whole features invisible until you
             happened to have the right model running. -->
        <router-link
          to="/stable-audio"
          class="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors"
          :class="route.path.startsWith('/stable-audio') ? 'border-accent1/60 bg-panel text-text' : 'border-border bg-panel-2 text-text-dim hover:text-text'"
        >
          {{ t('header.beats') }}
        </router-link>
        <router-link
          to="/ace-step/lora"
          class="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors"
          :class="route.path.startsWith('/ace-step/lora') ? 'border-accent1/60 bg-panel text-text' : 'border-border bg-panel-2 text-text-dim hover:text-text'"
        >
          {{ t('header.lora') }}
        </router-link>
        <router-link
          to="/voices"
          class="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors"
          :class="route.path.startsWith('/voices') ? 'border-accent1/60 bg-panel text-text' : 'border-border bg-panel-2 text-text-dim hover:text-text'"
        >
          {{ t('header.voices') }}
        </router-link>
        <router-link
          to="/separation"
          class="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors"
          :class="route.path.startsWith('/separation') ? 'border-accent1/60 bg-panel text-text' : 'border-border bg-panel-2 text-text-dim hover:text-text'"
        >
          {{ t('header.separation') }}
        </router-link>
        <button
          v-for="id in MODEL_IDS"
          :key="id"
          type="button"
          class="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors"
          :class="route.name === MODEL_ROUTES[id] ? 'border-accent1/60 bg-panel text-text' : 'border-border bg-panel-2 text-text-dim hover:text-text'"
          @click="onSelect(id)"
        >
          <span class="h-2 w-2 rounded-full" :class="LED_CLASSES[statusOf(id)]"></span>
          <span>{{ MODEL_LABELS[id] }}</span>
          <span class="text-xs text-text-dim">{{ t(STATUS_LABEL_KEYS[statusOf(id)]) }}</span>
        </button>
        <select
          class="rounded-lg border border-border bg-panel-2 px-2 py-2 text-sm text-text"
          :value="currentLocale()"
          @change="onLocaleChange"
        >
          <option v-for="loc in LOCALES" :key="loc.code" :value="loc.code">{{ loc.label }}</option>
        </select>
      </nav>
    </div>
    <p v-if="orchestrator.switchError" class="border-t border-status-failed/30 bg-status-failed/10 px-4 py-2 text-xs whitespace-pre-line text-status-failed sm:px-6">
      {{ orchestrator.switchError }}
    </p>
  </header>
</template>
