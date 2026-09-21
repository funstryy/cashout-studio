<script setup lang="ts">
/**
 * Insert a plugin on a track.
 *
 * Settings are made in the plugin's own window, opened by the host process.
 * A plugin draws its own interface, and for anything with a wavetable
 * display, a keyboard or a preset browser - Serum, Zenology - a column of
 * generic sliders built from the parameter list is not a substitute; it is
 * the same list a DAW hides behind "show generic editor" for the rare plugin
 * that has no interface at all. That fallback is here too, for those.
 *
 * "Insert" still means printed, not monitored: once the window is closed the
 * clip is rendered through the plugin with those settings and replaced. A VST
 * is machine code and cannot run inside the browser that plays the timeline.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as pluginsApi from '../../api/plugins'
import type { InstalledPlugin, PluginParameter } from '../../api/plugins'

const props = defineProps<{
  trackTitle: string
  slotKey: string
  /** Opened from a plugin already on the track - go straight to it. */
  preselectPath?: string | null
}>()
const emit = defineEmits<{
  close: []
  apply: [payload: {
    pluginPath: string
    params: Record<string, number>
    stateKey: string
    label: string
  }]
}>()

const { t } = useI18n()

const inventory = ref<InstalledPlugin[]>([])
const hostAvailable = ref(true)
const loading = ref(true)
const loadError = ref('')
const filter = ref('')

const selected = ref<InstalledPlugin | null>(null)
const description = ref<pluginsApi.PluginDescription | null>(null)
const describing = ref(false)
const values = ref<Record<number, number>>({})

const editorJobId = ref<string | null>(null)
const editorOpen = ref(false)
const editorError = ref('')
const stateSaved = ref(false)
const showGenericEditor = ref(false)

let pollTimer: number | undefined

/** One slot: this track, this plugin. Reopening the window finds it as it
 *  was left, and a second plugin on the same track keeps its own settings. */
const stateKey = computed(() => (selected.value ? `${props.slotKey}__${selected.value.name}` : ''))

const visible = computed(() => {
  const needle = filter.value.trim().toLowerCase()
  if (!needle) return inventory.value
  return inventory.value.filter((p) => p.name.toLowerCase().includes(needle))
})

async function load() {
  loading.value = true
  try {
    const result = await pluginsApi.listPlugins()
    // VST2 is intentionally absent: Steinberg withdrew that SDK licence, so
    // there is no lawful way to host those, and listing them here would only
    // promise something the Apply button cannot deliver.
    inventory.value = result.vst3
    hostAvailable.value = result.host_available
    if (props.preselectPath) {
      const already = inventory.value.find((p) => p.path === props.preselectPath)
      if (already) await select(already)
    }
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

async function select(plugin: InstalledPlugin) {
  selected.value = plugin
  description.value = null
  values.value = {}
  editorError.value = ''
  showGenericEditor.value = false
  stateSaved.value = false
  describing.value = true
  try {
    const info = await pluginsApi.describePlugin(plugin.path)
    description.value = info
    for (const param of info.parameters ?? []) {
      values.value[param.index] = param.default
    }
    stateSaved.value = (await pluginsApi.pluginStateSaved(stateKey.value)).saved
  } catch (err) {
    description.value = { ok: false, error: err instanceof Error ? err.message : String(err) }
  } finally {
    describing.value = false
  }
}

async function openEditor() {
  if (!selected.value || editorOpen.value) return
  editorError.value = ''
  try {
    const started = await pluginsApi.openPluginEditor({
      plugin_path: selected.value.path,
      state_key: stateKey.value,
    })
    if (!started.job_id) throw new Error(started.error || t('plugins.editorFailed'))
    editorJobId.value = started.job_id
    editorOpen.value = true
    poll()
  } catch (err) {
    editorError.value = err instanceof Error ? err.message : String(err)
  }
}

/** The window lives in another process, so the only way to know it closed is
 *  to ask. Once it has, its settings are on disk and a print will use them. */
function poll() {
  window.clearTimeout(pollTimer)
  pollTimer = window.setTimeout(async () => {
    const id = editorJobId.value
    if (!id) return
    try {
      const job = await pluginsApi.pluginEditorStatus(id)
      if (job.status === 'queued' || job.status === 'running') {
        poll()
        return
      }
      editorOpen.value = false
      editorJobId.value = null
      if (job.status === 'failed') {
        editorError.value = job.error || t('plugins.editorFailed')
        // A plugin with no interface of its own is not a failure - it is the
        // one case the numbered sliders are actually for.
        showGenericEditor.value = true
      } else if (job.status === 'done') {
        stateSaved.value = true
      }
    } catch (err) {
      editorOpen.value = false
      editorError.value = err instanceof Error ? err.message : String(err)
    }
  }, 700)
}

async function forceCloseEditor() {
  if (!editorJobId.value) return
  await pluginsApi.closePluginEditor(editorJobId.value)
}

function apply() {
  if (!selected.value) return
  const params: Record<string, number> = {}
  // Only when the user worked the generic sliders. Sending defaults alongside
  // a saved state would overwrite half of what the plugin's own window set.
  if (showGenericEditor.value) {
    for (const [index, value] of Object.entries(values.value)) params[index] = value
  }
  emit('apply', {
    pluginPath: selected.value.path,
    params,
    stateKey: stateKey.value,
    label: selected.value.name,
  })
}

function displayValue(param: PluginParameter): string {
  const value = values.value[param.index] ?? param.default
  return param.steps > 0 ? String(Math.round(value * param.steps)) : value.toFixed(2)
}

watch(() => props.trackTitle, load, { immediate: true })
onBeforeUnmount(() => window.clearTimeout(pollTimer))
</script>

<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" @click.self="emit('close')">
    <div class="flex max-h-[85vh] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-border bg-panel">
      <header class="flex items-center justify-between border-b border-border px-4 py-3">
        <div>
          <h2 class="text-sm font-semibold text-text">{{ t('plugins.title') }}</h2>
          <p class="text-xs text-text-dim">{{ t('plugins.onTrack', { track: trackTitle }) }}</p>
        </div>
        <button type="button" class="text-text-dim hover:text-text" @click="emit('close')">✕</button>
      </header>

      <p v-if="!hostAvailable" class="border-b border-status-failed/30 bg-status-failed/10 px-4 py-2 text-xs text-status-failed">
        {{ t('plugins.hostMissing') }}
      </p>

      <div class="grid min-h-0 flex-1 grid-cols-[minmax(0,18rem)_minmax(0,1fr)]">
        <div class="flex min-h-0 flex-col border-r border-border">
          <input
            v-model="filter"
            type="text"
            :placeholder="t('plugins.search')"
            class="m-2 rounded-lg border border-border bg-panel-2 px-2 py-1.5 text-xs text-text"
          />
          <p v-if="loading" class="px-3 text-xs text-text-dim">{{ t('common.loading') }}</p>
          <p v-else-if="loadError" class="px-3 text-xs text-status-failed">{{ loadError }}</p>
          <ul v-else class="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
            <li v-for="plugin in visible" :key="plugin.path">
              <button
                type="button"
                class="w-full truncate rounded px-2 py-1.5 text-left text-xs transition-colors"
                :class="selected?.path === plugin.path ? 'bg-accent1/20 text-text' : 'text-text-dim hover:text-text'"
                @click="select(plugin)"
              >
                {{ plugin.name }}
              </button>
            </li>
          </ul>
        </div>

        <div class="min-h-0 overflow-y-auto p-4">
          <p v-if="!selected" class="text-xs text-text-dim">{{ t('plugins.pick') }}</p>
          <template v-else>
            <p v-if="describing" class="text-xs text-text-dim">{{ t('plugins.loadingPlugin') }}</p>
            <template v-else-if="description?.ok">
              <h3 class="text-sm font-semibold text-text">{{ description.name }}</h3>
              <p class="text-xs text-text-dim">
                {{ description.vendor }} · {{ description.category }} ·
                {{ description.audio_inputs }} in / {{ description.audio_outputs }} out
              </p>

              <div class="mt-4 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  class="rounded-lg border border-accent1/60 bg-accent1/10 px-4 py-2 text-sm font-medium text-text disabled:opacity-50"
                  :disabled="!hostAvailable || editorOpen"
                  @click="openEditor"
                >
                  {{ editorOpen ? t('plugins.editorOpen') : t('plugins.openEditor') }}
                </button>
                <button
                  v-if="editorOpen"
                  type="button"
                  class="rounded-lg border border-border px-3 py-2 text-xs text-text-dim hover:text-text"
                  @click="forceCloseEditor"
                >
                  {{ t('plugins.forceClose') }}
                </button>
                <span v-else-if="stateSaved" class="text-xs text-status-done">{{ t('plugins.settingsSaved') }}</span>
                <span v-else class="text-xs text-text-dim">{{ t('plugins.noSettingsYet') }}</span>
              </div>
              <p class="mt-2 text-[11px] text-text-dim">
                {{ editorOpen ? t('plugins.editorOpenNote') : t('plugins.openEditorNote') }}
              </p>
              <p v-if="editorError" class="mt-2 whitespace-pre-line rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">
                {{ editorError }}
              </p>

              <div v-if="description.parameters?.length" class="mt-5 border-t border-border pt-3">
                <button
                  type="button"
                  class="text-xs text-text-dim underline-offset-2 hover:text-text hover:underline"
                  @click="showGenericEditor = !showGenericEditor"
                >
                  {{ showGenericEditor ? t('plugins.hideGeneric') : t('plugins.showGeneric') }}
                </button>
                <template v-if="showGenericEditor">
                  <p class="mt-2 text-[11px] text-text-dim">{{ t('plugins.genericNote') }}</p>
                  <div class="mt-3 space-y-3">
                    <div v-for="param in description.parameters" :key="param.index" class="space-y-1">
                      <div class="flex justify-between text-xs">
                        <span class="text-text">{{ param.title }}</span>
                        <span class="tabular-nums text-text-dim">{{ displayValue(param) }} {{ param.units }}</span>
                      </div>
                      <input
                        v-model.number="values[param.index]"
                        type="range" min="0" max="1" step="0.01"
                        class="w-full"
                      />
                    </div>
                  </div>
                </template>
              </div>
              <p v-else class="mt-5 text-xs text-text-dim">{{ t('plugins.noParams') }}</p>

              <button
                type="button"
                class="accent-gradient mt-5 rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                :disabled="!hostAvailable || editorOpen"
                @click="apply"
              >
                {{ t('plugins.apply') }}
              </button>
              <p class="mt-2 text-[11px] text-text-dim">{{ t('plugins.printNote') }}</p>
            </template>
            <p v-else class="whitespace-pre-line rounded-lg bg-status-failed/10 p-3 text-xs text-status-failed">
              {{ description?.error || t('plugins.loadFailed') }}
            </p>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>
