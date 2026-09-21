<script setup lang="ts">
/**
 * The file browser.
 *
 * FL Studio keeps this on the right; it lives on the left here because the
 * studio already has a left rail and putting a second vertical panel on the
 * opposite side would sandwich the arrangement between two columns of
 * chrome. Same job either way: audition a sample without importing it, then
 * drag it where it goes.
 *
 * Only folders the user has allowed are reachable - see backend/browser.py.
 * A tree rooted at C:\\ is both a liability once the studio listens on a
 * network and useless for finding a kick.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiFetch, apiJson } from '../../api/http'

const props = withDefaults(
  defineProps<{
    /** Show the pick button without waiting for a hover, and call it
     *  something other than "add to the timeline". Reused contexts - the
     *  separation lab imports with this - need both. */
    alwaysShowPick?: boolean
    pickLabel?: string
  }>(),
  { alwaysShowPick: false, pickLabel: '' },
)

const emit = defineEmits<{
  /** Chosen for import - the page decides whether that means a new lane, a
   *  rack channel, or a clip at the playhead. */
  pick: [payload: { path: string; name: string }]
}>()

const { t } = useI18n()

interface Root {
  key: string
  label: string
  path: string
  removable: boolean
  exists: boolean
}

interface Entry {
  name: string
  path: string
  is_dir: boolean
  size: number
  kind: 'dir' | 'audio' | 'midi'
}

const roots = ref<Root[]>([])
const cwd = ref<string | null>(null)
const entries = ref<Entry[]>([])
const query = ref('')
const searching = ref(false)
const loading = ref(false)
const error = ref('')
const newRoot = ref('')
const previewing = ref<string | null>(null)

let audio: HTMLAudioElement | null = null
let searchTimer: number | undefined

/** Breadcrumbs, but only back as far as the root that contains this path -
 *  walking above it would offer a folder the API will refuse. */
const trail = computed(() => {
  if (!cwd.value) return []
  const root = roots.value.find((r) => cwd.value!.startsWith(r.path))
  if (!root) return []
  const rest = cwd.value.slice(root.path.length).split(/[\\/]/).filter(Boolean)
  const crumbs = [{ label: root.label, path: root.path }]
  let accumulated = root.path
  for (const part of rest) {
    accumulated = `${accumulated}\\${part}`
    crumbs.push({ label: part, path: accumulated })
  }
  return crumbs
})

async function loadRoots() {
  try {
    roots.value = (await apiFetch<{ roots: Root[] }>('/api/browser/roots')).roots
  } catch (err) {
    error.value = message(err)
  }
}

async function open(path: string) {
  loading.value = true
  error.value = ''
  searching.value = false
  query.value = ''
  try {
    const result = await apiFetch<{ path: string; entries: Entry[] }>(
      `/api/browser/list?path=${encodeURIComponent(path)}`,
    )
    cwd.value = result.path
    entries.value = result.entries
  } catch (err) {
    error.value = message(err)
  } finally {
    loading.value = false
  }
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

/** Debounced: this fires per keystroke and walks several folder trees. */
watch(query, (value) => {
  window.clearTimeout(searchTimer)
  if (value.trim().length < 2) {
    searching.value = false
    if (cwd.value) void open(cwd.value)
    return
  }
  searchTimer = window.setTimeout(async () => {
    loading.value = true
    try {
      const result = await apiFetch<{ entries: Entry[] }>(
        `/api/browser/search?q=${encodeURIComponent(value.trim())}`,
      )
      entries.value = result.entries
      searching.value = true
    } catch (err) {
      error.value = message(err)
    } finally {
      loading.value = false
    }
  }, 300)
})

/**
 * Click to audition, click again to stop.
 *
 * Nothing is imported by previewing - that is the point of a browser. The
 * file streams from the API rather than being copied into the library, so
 * hearing forty kicks does not leave forty tracks behind.
 */
function preview(entry: Entry) {
  if (entry.kind !== 'audio') return
  if (previewing.value === entry.path) {
    stopPreview()
    return
  }
  stopPreview()
  audio = new Audio(`/api/browser/file?path=${encodeURIComponent(entry.path)}`)
  audio.volume = 0.85
  audio.addEventListener('ended', stopPreview, { once: true })
  void audio.play().catch(stopPreview)
  previewing.value = entry.path
}

function stopPreview() {
  audio?.pause()
  audio = null
  previewing.value = null
}

function onDragStart(event: DragEvent, entry: Entry) {
  // Both formats: the studio's own drop targets read the JSON, and a plain
  // text path is what everything else understands.
  event.dataTransfer?.setData('application/x-cashout-file', JSON.stringify(entry))
  event.dataTransfer?.setData('text/plain', entry.path)
}

async function addRoot() {
  const path = newRoot.value.trim()
  if (!path) return
  try {
    roots.value = (await apiJson<{ roots: Root[] }>('/api/browser/roots', { path })).roots
    newRoot.value = ''
  } catch (err) {
    error.value = message(err)
  }
}

async function dropRoot(root: Root) {
  try {
    roots.value = (
      await apiFetch<{ roots: Root[] }>(`/api/browser/roots?path=${encodeURIComponent(root.path)}`, {
        method: 'DELETE',
      })
    ).roots
    if (cwd.value?.startsWith(root.path)) {
      cwd.value = null
      entries.value = []
    }
  } catch (err) {
    error.value = message(err)
  }
}

function sizeLabel(bytes: number): string {
  if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1000))} KB`
}

onMounted(loadRoots)
</script>

<template>
  <aside class="card flex min-h-0 w-full flex-col overflow-hidden">
    <header class="border-b border-border px-2.5 py-2">
      <div class="flex items-center gap-2">
        <h3 class="text-xs font-semibold text-text">{{ t('browser.title') }}</h3>
        <button
          v-if="cwd"
          type="button"
          class="ml-auto text-[11px] text-text-dim hover:text-text"
          @click="cwd = null; entries = []; query = ''"
        >
          {{ t('browser.allFolders') }}
        </button>
      </div>
      <input
        v-model="query"
        type="text"
        :placeholder="t('browser.search')"
        class="mt-1.5 w-full rounded border border-border bg-panel-2 px-2 py-1 text-[11px] text-text"
      />
    </header>

    <!-- Breadcrumbs -->
    <div v-if="trail.length && !searching" class="flex flex-wrap items-center gap-0.5 px-2.5 py-1 text-[10px]">
      <template v-for="(crumb, index) in trail" :key="crumb.path">
        <button
          type="button"
          class="truncate text-text-dim hover:text-accent1"
          @click="open(crumb.path)"
        >{{ crumb.label }}</button>
        <span v-if="index < trail.length - 1" class="text-text-faint">/</span>
      </template>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
      <p v-if="loading" class="px-1 py-2 text-[11px] text-text-faint">{{ t('common.loading') }}</p>
      <p v-else-if="error" class="px-1 py-2 text-[11px] text-status-failed">{{ error }}</p>

      <!-- Root list -->
      <ul v-else-if="!cwd && !searching" class="space-y-0.5">
        <li v-for="root in roots" :key="root.key" class="group flex items-center">
          <button
            type="button"
            class="flex min-w-0 flex-1 items-center gap-1.5 rounded px-1.5 py-1 text-left text-[11px] text-text-dim hover:bg-panel-2 hover:text-text"
            @click="open(root.path)"
          >
            <span class="text-accent2">▸</span>
            <span class="truncate">{{ root.label }}</span>
          </button>
          <button
            v-if="root.removable"
            type="button"
            class="px-1 text-[10px] text-text-faint opacity-0 transition-opacity group-hover:opacity-100 hover:text-status-failed"
            :title="t('browser.removeFolder')"
            @click="dropRoot(root)"
          >✕</button>
        </li>
      </ul>

      <!-- Directory / search results -->
      <ul v-else class="space-y-0.5">
        <li v-if="!entries.length" class="px-1 py-2 text-[11px] text-text-faint">
          {{ searching ? t('browser.noMatches') : t('browser.emptyFolder') }}
        </li>
        <li
          v-for="entry in entries"
          :key="entry.path"
          class="group flex items-center gap-1 rounded px-1.5 py-1 hover:bg-panel-2"
          :draggable="!entry.is_dir"
          @dragstart="onDragStart($event, entry)"
        >
          <button
            v-if="entry.is_dir"
            type="button"
            class="flex min-w-0 flex-1 items-center gap-1.5 text-left text-[11px] text-text-dim hover:text-text"
            @click="open(entry.path)"
          >
            <span class="text-accent2">▸</span>
            <span class="truncate">{{ entry.name }}</span>
          </button>

          <template v-else>
            <button
              type="button"
              class="shrink-0 text-[10px]"
              :class="previewing === entry.path ? 'text-accent1' : 'text-text-faint hover:text-accent1'"
              :title="t('browser.preview')"
              @click="preview(entry)"
            >{{ previewing === entry.path ? '■' : '▶' }}</button>
            <span class="min-w-0 flex-1 truncate text-[11px] text-text-dim" :title="entry.path">
              {{ entry.name }}
            </span>
            <span class="shrink-0 text-[9px] tabular-nums text-text-faint">{{ sizeLabel(entry.size) }}</span>
            <button
              type="button"
              class="shrink-0 rounded px-1 text-[10px] transition-opacity hover:text-accent1"
              :class="props.alwaysShowPick
                ? 'text-accent1 opacity-100'
                : 'text-text-faint opacity-0 group-hover:opacity-100'"
              :title="props.pickLabel || t('browser.add')"
              @click="emit('pick', { path: entry.path, name: entry.name })"
            >+</button>
          </template>
        </li>
      </ul>
    </div>

    <footer class="border-t border-border px-2 py-1.5">
      <div class="flex gap-1">
        <input
          v-model="newRoot"
          type="text"
          :placeholder="t('browser.addFolder')"
          class="min-w-0 flex-1 rounded border border-border bg-panel-2 px-1.5 py-1 text-[10px] text-text"
          @keyup.enter="addRoot"
        />
        <button
          type="button"
          class="rounded border border-border px-2 text-[10px] text-text-dim hover:text-text"
          @click="addRoot"
        >+</button>
      </div>
      <p class="mt-1 text-[9px] leading-snug text-text-faint">{{ t('browser.dragHint') }}</p>
    </footer>
  </aside>
</template>
