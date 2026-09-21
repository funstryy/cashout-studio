<script setup lang="ts">
/**
 * The dashboard.
 *
 * Replaces a "choose a model" splash that had stopped being true: the studio
 * grew a DAW, a plugin host, a voice lab and a dataset builder, and none of
 * them are a model you pick. What someone actually wants on opening the app
 * is to get back into a project, see whether the machine can take a job right
 * now, and start the one thing they came to do.
 *
 * The system card is measured, not decorated - see backend/system_stats.py.
 * Nulls render as "-" rather than zero, because a failed counter and an idle
 * GPU are different facts and the widget exists to tell them apart.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import PageFrame from '../components/shared/PageFrame.vue'
import { useRouter } from 'vue-router'
import { useOrchestratorStore } from '../stores/orchestrator'
import * as projectsApi from '../api/projects'
import * as tracksApi from '../api/tracks'
import * as systemApi from '../api/system'
import type { ProjectSummary } from '../api/projects'
import type { SystemSnapshot } from '../api/system'

const { t } = useI18n()
const router = useRouter()
const orchestrator = useOrchestratorStore()

const projects = ref<ProjectSummary[]>([])
const tracks = ref<tracksApi.SavedTrack[]>([])
const trackCount = ref<number | null>(null)

/** Where a track came from, for the badge. Origins the library actually
 *  records - inventing a label for one it does not would be worse than
 *  showing the raw value. */
const ORIGIN_LABELS: Record<string, string> = {
  ace_step: 'ACE-Step',
  yue2: 'YuE2',
  stable_audio: 'Stable Audio',
  treblo: 'Treblo',
  voices: 'Voice',
  plugins: 'Plugin',
  editor: 'DAW',
  upload: 'Import',
}
const system = ref<SystemSnapshot | null>(null)
let timer: number | undefined

interface QuickAction {
  key: string
  to: string
  accent: string
  path: string
}

const QUICK_ACTIONS: QuickAction[] = [
  { key: 'home.actionNewProject', to: '/editor/new', accent: 'var(--color-accent1)',
    path: 'M12 5v14M5 12h14' },
  { key: 'home.actionBeat', to: '/stable-audio', accent: 'var(--color-accent2)',
    path: 'M12 3a9 9 0 1 0 9 9h-2a7 7 0 1 1-7-7z' },
  { key: 'home.actionVoice', to: '/ace-step/lora', accent: '#a78bfa',
    path: 'M12 14a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v5a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.9V21h2v-3.1A7 7 0 0 0 19 11z' },
  { key: 'home.actionSeparate', to: '/separation', accent: '#f0b429',
    path: 'M3 5h18v2H3zm0 6h8v2H3zm10 0h8v2h-8zM3 17h18v2H3z' },
]

function bytes(value: number | null | undefined): string {
  if (value == null) return '-'
  const gb = value / 1_000_000_000
  if (gb >= 1) return `${gb.toFixed(1)} GB`
  return `${Math.round(value / 1_000_000)} MB`
}

const vramCaption = computed(() => {
  const gpu = system.value?.gpu
  if (!gpu?.vram_used_bytes) return undefined
  return gpu.vram_total_bytes
    ? `${bytes(gpu.vram_used_bytes)} / ${bytes(gpu.vram_total_bytes)}`
    : bytes(gpu.vram_used_bytes)
})

const ramCaption = computed(() => {
  const snapshot = system.value
  if (!snapshot?.ram_total_bytes) return undefined
  return `${bytes(snapshot.ram_used_bytes)} / ${bytes(snapshot.ram_total_bytes)}`
})

const ramPercent = computed(() => {
  const snapshot = system.value
  if (!snapshot?.ram_total_bytes || snapshot.ram_used_bytes == null) return null
  return (snapshot.ram_used_bytes / snapshot.ram_total_bytes) * 100
})

const runningEngines = computed(() =>
  Object.entries(orchestrator.statuses || {})
    .filter(([, value]) => value?.status === 'running')
    .map(([id]) => id),
)

function relative(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const minutes = Math.round((Date.now() - then) / 60000)
  if (minutes < 1) return t('home.justNow')
  if (minutes < 60) return t('home.minutesAgo', { n: minutes })
  const hours = Math.round(minutes / 60)
  if (hours < 24) return t('home.hoursAgo', { n: hours })
  return t('home.daysAgo', { n: Math.round(hours / 24) })
}

async function load() {
  try {
    const list = await projectsApi.listProjects()
    projects.value = list
      .slice()
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
      .slice(0, 6)
  } catch {
    // An empty project list is a normal first-run state, not an error worth
    // shouting about on the dashboard.
  }
  try {
    const list = await tracksApi.listTracks()
    trackCount.value = list.length
    // listTracks returns newest first, which is the order worth showing.
    tracks.value = list.slice(0, 8)
  } catch {
    trackCount.value = null
  }
}

async function refreshSystem() {
  try {
    system.value = await systemApi.systemSnapshot()
  } catch {
    system.value = null
  }
}

onMounted(() => {
  void load()
  void refreshSystem()
  // Every four seconds: the GPU probe is cached for 2.5s server-side, so
  // this is roughly one real measurement per tick without hammering it.
  timer = window.setInterval(refreshSystem, 4000)
})
onBeforeUnmount(() => window.clearInterval(timer))

/*
 * Audition from the list.
 *
 * This was a <audio controls> per row. Native controls are the single most
 * page-like element a desktop app can put on screen - they are a different
 * widget set, a different size and a different colour on every platform -
 * and eight of them stacked down a list is a browser, not a studio. One
 * shared element plays one track at a time, which is also the behaviour
 * you want: starting a second preview should stop the first.
 */
const previewing = ref<number | null>(null)
let preview: HTMLAudioElement | null = null

function togglePreview(id: number) {
  if (previewing.value === id) {
    preview?.pause()
    previewing.value = null
    return
  }
  preview?.pause()
  preview = new Audio(`/api/tracks/${id}/audio`)
  preview.addEventListener('ended', () => { previewing.value = null })
  preview.addEventListener('error', () => { previewing.value = null })
  void preview.play().then(() => { previewing.value = id }).catch(() => {
    previewing.value = null
  })
}

onBeforeUnmount(() => preview?.pause())
</script>

<template>
  <PageFrame :title="t('home.welcome')" :subtitle="t('home.welcomeSub')">
    <template #actions>
      <span
        class="led h-[6px] w-[6px]"
        :style="{
          background: runningEngines.length ? 'var(--color-status-done)' : '#0c1219',
          color: runningEngines.length ? 'var(--color-status-done)' : 'transparent',
        }"
      ></span>
      <span class="text-[11px] text-text-dim">
        {{ runningEngines.length ? t('home.enginesUp', { n: runningEngines.length }) : t('home.enginesDown') }}
      </span>
    </template>


    <div class="grid gap-2 xl:grid-cols-[minmax(0,1fr)_17rem]">
      <div class="space-y-2">
        <!-- Launchers. One line each: a start page is a list of ways in,
             not a gallery of feature tiles. -->
        <section class="grid gap-1 sm:grid-cols-2 lg:grid-cols-4">
          <button
            v-for="action in QUICK_ACTIONS"
            :key="action.to"
            type="button"
            class="key flex items-center gap-2 px-2.5 py-2 text-left"
            @click="router.push(action.to)"
          >
            <svg viewBox="0 0 24 24" class="h-4 w-4 shrink-0" aria-hidden="true">
              <path :d="action.path" :fill="action.accent" />
            </svg>
            <span class="min-w-0">
              <span class="block truncate text-[12px] font-medium text-text">{{ t(`${action.key}.title`) }}</span>
              <span class="block truncate text-[10px] text-text-faint">{{ t(`${action.key}.sub`) }}</span>
            </span>
          </button>
        </section>

        <!-- Recent projects.
             These were tiles with a gradient generated from the project id,
             which is cover art for something that has no cover. What you
             actually want from a recent list is a name and a date, close
             enough together to scan - so it is a table. -->
        <section class="card">
          <header class="rack-strip flex items-baseline justify-between px-2.5 py-1.5">
            <h2 class="engraved text-[10px] font-semibold uppercase text-accent1">
              {{ t('home.recentProjects') }}
            </h2>
            <router-link to="/editor" class="text-[10px] text-text-dim hover:text-text">
              {{ t('home.viewAll') }}
            </router-link>
          </header>

          <table v-if="projects.length" class="w-full">
            <tbody>
              <tr
                v-for="project in projects"
                :key="project.id"
                class="cursor-pointer border-b border-black/60 last:border-0 hover:bg-accent1/10"
                @click="router.push(`/editor/${project.id}`)"
              >
                <td class="truncate px-2.5 py-1 text-[12px] text-text">{{ project.name }}</td>
                <td class="w-32 whitespace-nowrap px-2.5 py-1 text-right text-[10px] tabular-nums text-text-faint">
                  {{ relative(project.updated_at) }}
                </td>
              </tr>
            </tbody>
          </table>

          <div v-else class="flex items-center gap-3 px-2.5 py-2.5">
            <p class="text-[11px] text-text-dim">{{ t('home.noProjects') }}</p>
            <router-link to="/editor/new" class="accent-gradient ml-auto px-3 py-1 text-[11px]">
              {{ t('home.actionNewProject.title') }}
            </router-link>
          </div>
        </section>

        <!-- Library -->
        <section v-if="tracks.length" class="card">
          <header class="rack-strip flex items-baseline justify-between px-2.5 py-1.5">
            <h2 class="engraved text-[10px] font-semibold uppercase text-accent1">
              {{ t('home.recentTracks') }}
            </h2>
            <span class="readout-dim text-[10px]">
              {{ trackCount == null ? '' : t('home.trackCount', { n: trackCount }) }}
            </span>
          </header>
          <table class="w-full">
            <tbody>
              <tr
                v-for="track in tracks"
                :key="track.id"
                class="border-b border-black/60 last:border-0"
              >
                <td class="w-8 py-1 pl-2">
                  <button
                    type="button"
                    class="key flex h-5 w-5 items-center justify-center"
                    :class="previewing === track.id ? 'key-on' : ''"
                    :title="t('waveformPlayer.play')"
                    @click="togglePreview(track.id)"
                  >
                    <svg viewBox="0 0 24 24" class="h-2.5 w-2.5" aria-hidden="true">
                      <path
                        v-if="previewing !== track.id"
                        d="M8 5v14l11-7z"
                        fill="currentColor"
                      />
                      <path v-else d="M7 5h4v14H7zm6 0h4v14h-4z" fill="currentColor" />
                    </svg>
                  </button>
                </td>
                <td class="w-20 py-1 pr-2">
                  <span class="engraved text-[9px] uppercase text-text-faint">
                    {{ ORIGIN_LABELS[track.model] || track.model }}
                  </span>
                </td>
                <td class="truncate py-1 pr-2.5 text-[12px] text-text">
                  {{ track.title || t('home.untitled') }}
                </td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>

      <!-- System.
           Two donuts with a big percentage in the middle is an infographic.
           A machine reports load on meters, laid out so the numbers line up
           in a column you can read down. -->
      <aside class="card self-start">
        <header class="rack-strip px-2.5 py-1.5">
          <h2 class="engraved text-[10px] font-semibold uppercase text-accent2">
            {{ t('home.system') }}
          </h2>
        </header>
        <div class="space-y-2 p-2.5">
          <p class="truncate text-[10px] text-text-faint" :title="system?.gpu.name || ''">
            {{ system?.gpu.name || t('home.gpuUnknown') }}
          </p>

          <div
            v-for="row in [
              { label: t('home.gpu'), pct: system?.gpu.utilisation ?? null, note: vramCaption },
              { label: t('home.cpu'), pct: system?.cpu_percent ?? null, note: '' },
              { label: t('home.ram'), pct: ramPercent, note: ramCaption },
            ]"
            :key="row.label"
          >
            <div class="flex items-baseline justify-between text-[10px]">
              <span class="engraved uppercase text-text-dim">{{ row.label }}</span>
              <span class="readout">{{ row.pct == null ? '-' : Math.round(row.pct) + '%' }}</span>
            </div>
            <div class="meter-well mt-0.5 h-[7px] w-full">
              <div
                class="meter-fill transition-all duration-500"
                :style="{
                  width: `${Math.min(100, row.pct ?? 0)}%`,
                  background: (row.pct ?? 0) > 85 ? 'var(--color-status-failed)' : 'var(--color-accent1)',
                  color: (row.pct ?? 0) > 85 ? 'var(--color-status-failed)' : 'var(--color-accent1)',
                }"
              />
            </div>
            <p v-if="row.note" class="mt-0.5 text-right text-[9px] tabular-nums text-text-faint">
              {{ row.note }}
            </p>
          </div>

          <div class="divider-engraved space-y-1 pt-2">
            <div class="flex justify-between text-[10px]">
              <span class="text-text-dim">{{ t('home.diskFree') }}</span>
              <span class="readout-dim">{{ bytes(system?.disk_free_bytes) }}</span>
            </div>
            <div class="flex justify-between text-[10px]">
              <span class="text-text-dim">{{ t('home.library') }}</span>
              <span class="readout-dim">
                {{ trackCount == null ? '-' : t('home.trackCount', { n: trackCount }) }}
              </span>
            </div>
          </div>

          <p v-if="!system" class="text-[10px] leading-snug text-text-faint">
            {{ t('home.systemUnavailable') }}
          </p>
        </div>
      </aside>
    </div>
  </PageFrame>
</template>
