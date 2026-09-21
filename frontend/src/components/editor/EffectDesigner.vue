<script setup lang="ts">
/**
 * Build your own plugin.
 *
 * Stack primitives, turn the knobs, hear it immediately, save it by name.
 * These run *live* - they are Web Audio nodes in the signal path, not a
 * render - which is the one thing the VST host cannot offer here, since
 * native code cannot execute inside the browser that plays the timeline.
 *
 * The description box is a rule table, not a model: the vocabulary for this
 * is small and stable ("warm", "lo-fi", "telephone", "wide" mean the same
 * thing to everyone), so a lookup gets it right instantly and offline. It
 * reports which words it recognised rather than implying it understood the
 * sentence, and everything it produces is meant to be dragged around
 * afterwards.
 */
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  EFFECTS,
  EFFECT_ORDER,
  buildChain,
  chainFromDescription,
  newEffect,
} from '../../audio/customEffects'
import type { BuiltChain, CustomPlugin, EffectKind, EffectNode } from '../../audio/customEffects'
import { getSharedAudioCtx } from '../../composables/audioPlayback'
import { apiFetch, apiJson } from '../../api/http'

const emit = defineEmits<{ saved: [plugin: CustomPlugin] }>()
const { t } = useI18n()

const chain = ref<EffectNode[]>([newEffect('filter')])
const name = ref('')
const description = ref('')
const matched = ref<string[]>([])
const saved = ref<CustomPlugin[]>([])
const editingId = ref<string | null>(null)
const error = ref('')
const auditioning = ref(false)

let audition: { source: AudioBufferSourceNode; built: BuiltChain } | null = null

const canSave = computed(() => name.value.trim().length > 0 && chain.value.length > 0)

function add(kind: EffectKind) {
  chain.value.push(newEffect(kind))
  refreshAudition()
}

function remove(index: number) {
  chain.value.splice(index, 1)
  refreshAudition()
}

function move(index: number, by: number) {
  const to = index + by
  if (to < 0 || to >= chain.value.length) return
  const [node] = chain.value.splice(index, 1)
  chain.value.splice(to, 0, node)
  refreshAudition()
}

function seed() {
  const result = chainFromDescription(description.value)
  chain.value = result.chain
  matched.value = result.matched
  if (!name.value.trim() && description.value.trim()) {
    name.value = description.value.trim().slice(0, 40)
  }
  refreshAudition()
}

/**
 * Loops pink-ish noise through the chain so a knob can be heard while it is
 * turned. Noise rather than a tone: a sine says nothing about a filter's
 * resonance or a compressor's release, and noise excites the whole spectrum
 * at once.
 */
function startAudition() {
  stopAudition()
  const ctx = getSharedAudioCtx()
  const seconds = 2
  const buffer = new AudioBuffer({
    numberOfChannels: 2,
    length: ctx.sampleRate * seconds,
    sampleRate: ctx.sampleRate,
  })
  for (let channel = 0; channel < 2; channel++) {
    const data = buffer.getChannelData(channel)
    let last = 0
    for (let i = 0; i < data.length; i++) {
      const white = Math.random() * 2 - 1
      // A one-pole smooth tilts white towards pink, which is far less
      // fatiguing to leave looping while you work.
      last = 0.97 * last + 0.03 * white
      data[i] = last * 3
    }
  }

  const built = buildChain(ctx, chain.value)
  const source = ctx.createBufferSource()
  source.buffer = buffer
  source.loop = true
  source.connect(built.input)
  built.output.connect(ctx.destination)
  source.start()
  audition = { source, built }
  auditioning.value = true
}

function stopAudition() {
  if (!audition) return
  try {
    audition.source.stop()
  } catch {
    // Already stopped.
  }
  audition.source.disconnect()
  audition.built.dispose()
  audition = null
  auditioning.value = false
}

/** Rebuilt rather than re-tuned: a chain can gain or lose nodes between
 *  edits, and rewiring live is far more code than a 20ms restart. */
function refreshAudition() {
  if (auditioning.value) startAudition()
}

async function load() {
  try {
    saved.value = (await apiFetch<{ plugins: CustomPlugin[] }>('/api/effects')).plugins
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function save() {
  if (!canSave.value) return
  error.value = ''
  try {
    const plugin = await apiJson<CustomPlugin>('/api/effects', {
      id: editingId.value ?? undefined,
      name: name.value.trim(),
      chain: chain.value,
    })
    editingId.value = plugin.id
    await load()
    emit('saved', plugin)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

function open(plugin: CustomPlugin) {
  // Deep-copied: editing a saved plugin must not mutate the list behind it
  // before the user has pressed save.
  chain.value = plugin.chain.map((node) => ({ ...node, values: { ...node.values } }))
  name.value = plugin.name
  editingId.value = plugin.id
  matched.value = []
  refreshAudition()
}

async function drop(plugin: CustomPlugin) {
  try {
    await apiFetch(`/api/effects/${plugin.id}`, { method: 'DELETE' })
    if (editingId.value === plugin.id) editingId.value = null
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

function fresh() {
  chain.value = [newEffect('filter')]
  name.value = ''
  editingId.value = null
  matched.value = []
  refreshAudition()
}

const FILTER_NAMES = ['Low-pass', 'High-pass', 'Band-pass', 'Bell']
function valueLabel(node: EffectNode, key: string, value: number): string {
  if (node.kind === 'filter' && key === 'type') return FILTER_NAMES[Math.round(value)] ?? ''
  const unit = EFFECTS[node.kind].params.find((p) => p.key === key)?.unit ?? ''
  const decimals = Math.abs(value) < 10 ? 2 : 0
  return `${value.toFixed(decimals)}${unit}`
}

void load()
onBeforeUnmount(stopAudition)
</script>

<template>
  <div class="space-y-3">
    <p class="text-xs text-text-dim">{{ t('designer.intro') }}</p>

    <!-- From words -->
    <div class="flex flex-wrap items-center gap-2">
      <input
        v-model="description"
        type="text"
        :placeholder="t('designer.describePlaceholder')"
        class="min-w-0 flex-1 rounded border border-border bg-panel-2 px-2 py-1.5 text-xs text-text"
        @keyup.enter="seed"
      />
      <button
        type="button"
        class="rounded border border-accent1/60 bg-accent1/10 px-3 py-1.5 text-xs text-text"
        @click="seed"
      >{{ t('designer.build') }}</button>
    </div>
    <p v-if="matched.length" class="text-[10px] text-text-faint">
      {{ t('designer.heard', { words: matched.join(', ') }) }}
    </p>

    <!-- Chain -->
    <div class="space-y-1.5">
      <div
        v-for="(node, index) in chain"
        :key="node.id"
        class="rounded border border-border bg-panel-2/50 p-2"
        :class="node.bypassed ? 'opacity-45' : ''"
      >
        <div class="flex items-center gap-1.5">
          <span class="text-[10px] tabular-nums text-text-faint">{{ index + 1 }}</span>
          <span class="text-xs font-medium text-accent1">{{ EFFECTS[node.kind].label }}</span>
          <span class="min-w-0 flex-1 truncate text-[10px] text-text-faint">{{ EFFECTS[node.kind].blurb }}</span>
          <button
            type="button"
            class="px-1 text-[10px] text-text-faint hover:text-text"
            :title="t('designer.bypass')"
            @click="node.bypassed = !node.bypassed; refreshAudition()"
          >{{ node.bypassed ? '○' : '●' }}</button>
          <button type="button" class="px-1 text-[10px] text-text-faint hover:text-text" @click="move(index, -1)">↑</button>
          <button type="button" class="px-1 text-[10px] text-text-faint hover:text-text" @click="move(index, 1)">↓</button>
          <button type="button" class="px-1 text-[10px] text-text-faint hover:text-status-failed" @click="remove(index)">✕</button>
        </div>

        <div class="mt-1.5 grid gap-x-3 gap-y-1 sm:grid-cols-2">
          <label v-for="param in EFFECTS[node.kind].params" :key="param.key" class="flex items-center gap-2">
            <span class="w-20 shrink-0 text-[10px] text-text-dim">{{ param.label }}</span>
            <input
              v-model.number="node.values[param.key]"
              type="range"
              :min="param.min" :max="param.max" :step="param.step"
              class="min-w-0 flex-1"
              @input="refreshAudition()"
            />
            <span class="w-16 shrink-0 text-right text-[10px] tabular-nums text-text-faint">
              {{ valueLabel(node, param.key, node.values[param.key]) }}
            </span>
          </label>
        </div>
      </div>
    </div>

    <!-- Add -->
    <div class="flex flex-wrap gap-1">
      <button
        v-for="kind in EFFECT_ORDER"
        :key="kind"
        type="button"
        class="rounded border border-border px-2 py-1 text-[10px] text-text-dim transition-colors hover:border-accent1/50 hover:text-text"
        @click="add(kind)"
      >+ {{ EFFECTS[kind].label }}</button>
    </div>

    <!-- Save / audition -->
    <div class="flex flex-wrap items-center gap-2 border-t border-border pt-2">
      <input
        v-model="name"
        type="text"
        :placeholder="t('designer.namePlaceholder')"
        class="w-44 rounded border border-border bg-panel-2 px-2 py-1.5 text-xs text-text"
      />
      <button
        type="button"
        class="accent-gradient rounded px-3 py-1.5 text-xs disabled:opacity-40"
        :disabled="!canSave"
        @click="save"
      >{{ editingId ? t('designer.update') : t('common.save') }}</button>
      <button
        type="button"
        class="rounded border border-border px-3 py-1.5 text-xs text-text-dim hover:text-text"
        @click="auditioning ? stopAudition() : startAudition()"
      >{{ auditioning ? t('designer.stopAudition') : t('designer.audition') }}</button>
      <button type="button" class="text-[11px] text-text-faint hover:text-text" @click="fresh">
        {{ t('designer.new') }}
      </button>
    </div>

    <p v-if="error" class="rounded bg-status-failed/10 p-2 text-[11px] text-status-failed">{{ error }}</p>

    <!-- Saved -->
    <div v-if="saved.length" class="border-t border-border pt-2">
      <p class="mb-1 text-[10px] font-semibold uppercase tracking-wider text-text-faint">
        {{ t('designer.yours') }}
      </p>
      <ul class="flex flex-wrap gap-1">
        <li v-for="plugin in saved" :key="plugin.id" class="group flex items-center">
          <button
            type="button"
            class="rounded-l border border-border px-2 py-1 text-[11px] transition-colors"
            :class="editingId === plugin.id ? 'border-accent1/60 bg-accent1/10 text-text' : 'text-text-dim hover:text-text'"
            @click="open(plugin)"
          >{{ plugin.name }}</button>
          <button
            type="button"
            class="rounded-r border border-l-0 border-border px-1 py-1 text-[10px] text-text-faint hover:text-status-failed"
            @click="drop(plugin)"
          >✕</button>
        </li>
      </ul>
    </div>
  </div>
</template>
