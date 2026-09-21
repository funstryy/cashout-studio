<script setup lang="ts">
/**
 * The native audio engine.
 *
 * This panel is where the studio stops being a web app. Playback in the
 * browser goes through Web Audio, which on this machine sits at 22ms in the
 * best case and drops buffers whenever the page does layout. The engine is
 * a separate C++ process on an MMCSS Pro Audio thread talking to WASAPI:
 * measured at 2.67ms with zero dropouts, and VST3 plugins running live in
 * the signal path rather than printed onto the clip afterwards.
 *
 * The numbers are on screen because in a DAW they are not diagnostics, they
 * are the thing you tune. Buffer size against CPU load against dropouts is
 * a decision the person at the desk makes, and they cannot make it without
 * seeing all three.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiFetch, apiJson } from '../../api/http'

interface EngineDevice {
  id: string
  name: string
  default: boolean
  mixRate: number
  channels: number
}

interface EngineStatus {
  ok?: boolean
  available?: boolean
  running?: boolean
  open?: boolean
  device?: string
  exclusive?: boolean
  sampleRate?: number
  bufferFrames?: number
  latencyMs?: number
  xruns?: number
  loadPeak?: number
  loadRecent?: number
  peakL?: number
  peakR?: number
  captureSeconds?: number
  error?: string
}

const { t } = useI18n()

const status = ref<EngineStatus>({})
const devices = ref<EngineDevice[]>([])
const deviceId = ref('')
const bufferFrames = ref(256)
const exclusive = ref(false)
const busy = ref(false)
const error = ref('')
const captured = ref('')

let poll: ReturnType<typeof setInterval> | null = null

// Two figures a DAW always shows together, because one without the other is
// meaningless: a 2.67ms buffer is only good news if the load fits inside it.
const latency = computed(() =>
  status.value.latencyMs ? `${status.value.latencyMs.toFixed(2)} ms` : '-',
)
const load = computed(() =>
  status.value.loadPeak !== undefined ? `${(status.value.loadPeak * 100).toFixed(0)}%` : '-',
)
const loadColour = computed(() => {
  const value = status.value.loadPeak ?? 0
  if (value > 0.9) return 'text-status-failed'
  if (value > 0.6) return 'text-status-queued'
  return 'text-accent1'
})

const BUFFERS = [64, 128, 256, 512, 1024]

async function refresh() {
  try {
    status.value = await apiFetch<EngineStatus>('/api/engine/status')
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
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
    await refresh()
  }
}

async function startEngine() {
  await run(async () => {
    await apiJson('/api/engine/start', {})
    const list = await apiFetch<{ devices: EngineDevice[] }>('/api/engine/devices')
    devices.value = list.devices
    deviceId.value = list.devices.find((d) => d.default)?.id ?? list.devices[0]?.id ?? ''
  })
}

async function openDevice() {
  await run(() =>
    apiJson('/api/engine/open', {
      device: deviceId.value,
      bufferFrames: bufferFrames.value,
      sampleRate: 48000,
      exclusive: exclusive.value,
    }),
  )
}

async function closeDevice() {
  await run(() => apiJson('/api/engine/close', {}))
}

async function keepLast() {
  captured.value = ''
  await run(async () => {
    const result = await apiJson<{ seconds: number; title: string }>('/api/engine/capture', {
      seconds: 30,
    })
    captured.value = t('engine.captured', { seconds: Math.round(result.seconds) })
  })
}

onMounted(async () => {
  await refresh()
  if (status.value.running) {
    try {
      devices.value = (await apiFetch<{ devices: EngineDevice[] }>('/api/engine/devices')).devices
      deviceId.value = devices.value.find((d) => d.default)?.id ?? ''
    } catch {
      // The engine went away between the two calls. refresh() will say so.
    }
  }
  // Only while something is actually open - polling a stopped engine is a
  // request a second for a row of dashes.
  poll = setInterval(() => {
    if (status.value.running) void refresh()
  }, 1000)
})

onBeforeUnmount(() => {
  if (poll) clearInterval(poll)
})
</script>

<template>
  <div class="space-y-3">
    <p class="text-xs text-text-dim">{{ t('engine.intro') }}</p>

    <!-- Not built -->
    <p v-if="status.available === false" class="rounded bg-status-queued/10 p-2 text-[11px] text-status-queued">
      {{ t('engine.notBuilt') }}
    </p>

    <!-- Not running -->
    <template v-else-if="!status.running">
      <button
        type="button"
        class="accent-gradient rounded px-3 py-1.5 text-xs disabled:opacity-40"
        :disabled="busy"
        @click="startEngine"
      >{{ t('engine.start') }}</button>
    </template>

    <template v-else>
      <!-- Readouts -->
      <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div class="rounded border border-border bg-panel-2/40 p-2">
          <p class="text-[9px] uppercase tracking-wider text-text-faint">{{ t('engine.latency') }}</p>
          <p class="font-mono text-sm text-accent1">{{ latency }}</p>
        </div>
        <div class="rounded border border-border bg-panel-2/40 p-2">
          <p class="text-[9px] uppercase tracking-wider text-text-faint">{{ t('engine.load') }}</p>
          <p class="font-mono text-sm" :class="loadColour">{{ load }}</p>
        </div>
        <div class="rounded border border-border bg-panel-2/40 p-2">
          <p class="text-[9px] uppercase tracking-wider text-text-faint">{{ t('engine.dropouts') }}</p>
          <p class="font-mono text-sm" :class="(status.xruns ?? 0) > 0 ? 'text-status-failed' : 'text-text'">
            {{ status.xruns ?? 0 }}
          </p>
        </div>
        <div class="rounded border border-border bg-panel-2/40 p-2">
          <p class="text-[9px] uppercase tracking-wider text-text-faint">{{ t('engine.buffer') }}</p>
          <p class="font-mono text-sm text-text">{{ status.bufferFrames ?? '-' }}</p>
        </div>
      </div>

      <p v-if="status.open" class="truncate text-[11px] text-text-dim">
        {{ status.device }} · {{ status.sampleRate }} Hz
        <span v-if="status.exclusive" class="text-accent2">· {{ t('engine.exclusiveTag') }}</span>
      </p>

      <!-- Device -->
      <div class="flex flex-wrap items-center gap-1.5">
        <select
          v-model="deviceId"
          class="min-w-0 flex-1 rounded border border-border bg-panel-2 px-2 py-1.5 text-xs text-text"
        >
          <option v-for="device in devices" :key="device.id" :value="device.id">
            {{ device.name }}{{ device.default ? ' ★' : '' }}
          </option>
        </select>
        <select
          v-model.number="bufferFrames"
          class="rounded border border-border bg-panel-2 px-2 py-1.5 text-xs text-text"
        >
          <option v-for="size in BUFFERS" :key="size" :value="size">{{ size }}</option>
        </select>
      </div>

      <label class="flex items-start gap-2 text-[11px] text-text-dim">
        <input v-model="exclusive" type="checkbox" class="mt-0.5" />
        <span>{{ t('engine.exclusiveHint') }}</span>
      </label>

      <div class="flex flex-wrap gap-1.5">
        <button
          type="button"
          class="accent-gradient rounded px-3 py-1.5 text-xs disabled:opacity-40"
          :disabled="busy || !deviceId"
          @click="openDevice"
        >{{ status.open ? t('engine.reopen') : t('engine.open') }}</button>
        <button
          v-if="status.open"
          type="button"
          class="rounded border border-border px-3 py-1.5 text-xs text-text-dim hover:text-text"
          :disabled="busy"
          @click="closeDevice"
        >{{ t('engine.closeDevice') }}</button>
      </div>

      <!-- The feature the ring buffer pays for -->
      <div v-if="status.open" class="rounded border border-accent2/40 bg-accent2/5 p-2">
        <p class="text-[11px] font-medium text-text">{{ t('engine.retroTitle') }}</p>
        <p class="mt-0.5 text-[10px] leading-snug text-text-dim">{{ t('engine.retroBlurb') }}</p>
        <button
          type="button"
          class="mt-1.5 rounded border border-accent2/60 bg-accent2/10 px-3 py-1.5 text-xs text-text disabled:opacity-40"
          :disabled="busy"
          @click="keepLast"
        >{{ t('engine.keepLast') }}</button>
        <p v-if="captured" class="mt-1 text-[11px] text-accent1">{{ captured }}</p>
      </div>
    </template>

    <p v-if="error" class="rounded bg-status-failed/10 p-2 text-[11px] text-status-failed">{{ error }}</p>
    <p v-else-if="status.error" class="text-[11px] text-text-faint">{{ status.error }}</p>
  </div>
</template>
