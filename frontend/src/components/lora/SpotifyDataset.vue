<script setup lang="ts">
/**
 * Build a LoRA dataset out of what you actually listen to.
 *
 * The audio never comes from Spotify - there is no API that returns it, and
 * ripping the stream is not something this app will do. What Spotify provides
 * is the tedious half: deciding *which* of your own files are worth training
 * on, ranked by what you actually play.
 *
 * Genres were meant to come from Spotify too. Measured against a real
 * Development Mode app, the artist endpoint is refused - so the style tags
 * field carries that instead, and the panel says plainly which happened
 * rather than quietly producing thinner captions.
 *
 * The flow is four steps because each one can fail in a way the user needs to
 * see: connect, load listening, match against local audio, build.
 */
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/spotify'
import type { ListeningTrack, MatchedTrack, MissingTrack, SpotifyStatus } from '../../api/spotify'

const emit = defineEmits<{
  /** Hands the finished dataset to the LoRA page's own fields. */
  dataset: [payload: { audioDir: string; datasetName: string }]
}>()

const { t } = useI18n()

const status = ref<SpotifyStatus | null>(null)
const clientIdInput = ref('')
const busy = ref('')
const error = ref('')

const source = ref<api.ListeningSource>('top_medium')
const tracks = ref<ListeningTrack[]>([])
const genreStatus = ref<api.GenreStatus>('skipped')

const folder = ref('')
const includeLibrary = ref(true)
const matched = ref<MatchedTrack[]>([])
const missing = ref<MissingTrack[]>([])
const searched = ref(0)

const datasetName = ref('spotify_lora')
const trigger = ref('')
const styleTags = ref('')
const includeArtist = ref(true)
const built = ref<{ file: string; caption: string; title: string }[]>([])

let pollTimer: number | undefined

const redirectUris = computed(() => {
  const port = window.location.port || '9000'
  const path = status.value?.redirect_path ?? '/api/spotify/callback'
  // Two, deliberately. The studio falls back to a random port when 9000 is
  // taken, and Spotify has a specific exception that lets a port-less
  // loopback URI match whatever port turns up at runtime.
  return [`http://127.0.0.1:${port}${path}`, `http://127.0.0.1${path}`]
})

async function refresh() {
  try {
    status.value = await api.spotifyStatus()
    if (status.value.client_id) clientIdInput.value = status.value.client_id
  } catch (err) {
    error.value = message(err)
  }
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

async function run<T>(label: string, work: () => Promise<T>): Promise<T | null> {
  busy.value = label
  error.value = ''
  try {
    return await work()
  } catch (err) {
    error.value = message(err)
    return null
  } finally {
    busy.value = ''
  }
}

async function saveClientId() {
  const value = clientIdInput.value.trim()
  if (!value) return
  const result = await run('config', () => api.saveClientId(value))
  if (result) status.value = result
}

async function connect() {
  const port = Number(window.location.port || 9000)
  const result = await run('login', () => api.spotifyLogin(port))
  if (!result) return
  if (!result.opened) {
    // The system browser refused to open - hand over the URL instead of
    // leaving them staring at a button that did nothing.
    error.value = t('spotify.openManually', { url: result.url })
  }
  poll()
}

/** The sign-in finishes in a different browser, so the only way to learn it
 *  worked is to keep asking. Gives up after five minutes. */
function poll(attempt = 0) {
  window.clearTimeout(pollTimer)
  if (attempt > 150) return
  pollTimer = window.setTimeout(async () => {
    await refresh()
    if (!status.value?.connected) poll(attempt + 1)
  }, 2000)
}

async function disconnect() {
  const result = await run('disconnect', () => api.spotifyDisconnect())
  if (result) {
    status.value = result
    tracks.value = []
    matched.value = []
    missing.value = []
  }
}

async function loadListening() {
  const result = await run('listening', () => api.fetchListening(source.value))
  if (!result) return
  tracks.value = result.tracks
  genreStatus.value = result.genre_status
  matched.value = []
  missing.value = []
}

async function matchLocal() {
  const result = await run('match', () =>
    api.matchTracks({
      tracks: tracks.value,
      folders: folder.value.split('\n').map((line) => line.trim()).filter(Boolean),
      include_library: includeLibrary.value,
    }),
  )
  if (!result) return
  matched.value = result.matched
  missing.value = result.missing
  searched.value = result.searched
}

async function build() {
  const result = await run('build', () =>
    api.buildDataset({
      dataset_name: datasetName.value,
      matched: matched.value,
      trigger: trigger.value.trim() || undefined,
      include_artist: includeArtist.value,
      style_tags: styleTags.value.split(',').map((tag) => tag.trim()).filter(Boolean),
    }),
  )
  if (!result) return
  built.value = result.files
  emit('dataset', { audioDir: result.audio_dir, datasetName: result.dataset_name })
}

function dropMatch(index: number) {
  matched.value.splice(index, 1)
}

onMounted(refresh)
onBeforeUnmount(() => window.clearTimeout(pollTimer))
</script>

<template>
  <section class="rounded-xl border border-border bg-panel p-4">
    <header class="flex flex-wrap items-center gap-2">
      <h3 class="text-sm font-semibold text-text">{{ t('spotify.title') }}</h3>
      <span v-if="status?.connected" class="rounded-full bg-status-done/20 px-2 py-0.5 text-[11px] text-status-done">
        {{ status.display_name || t('spotify.connected') }}
      </span>
      <button
        v-if="status?.connected"
        type="button"
        class="ml-auto text-xs text-text-dim hover:text-text"
        @click="disconnect"
      >
        {{ t('spotify.disconnect') }}
      </button>
    </header>

    <p class="mt-1 text-xs text-text-dim">{{ t('spotify.intro') }}</p>

    <!-- 1. Client ID -->
    <div v-if="!status?.configured" class="mt-4 space-y-2 rounded-lg border border-border bg-panel-2 p-3">
      <p class="text-xs font-medium text-text">{{ t('spotify.setupTitle') }}</p>
      <ol class="list-decimal space-y-1 pl-4 text-xs text-text-dim">
        <li>{{ t('spotify.setupStep1') }}</li>
        <li>
          {{ t('spotify.setupStep2') }}
          <div class="mt-1 space-y-1">
            <code
              v-for="uri in redirectUris"
              :key="uri"
              class="block select-all rounded bg-panel px-1.5 py-1 text-[11px] text-text"
            >{{ uri }}</code>
          </div>
        </li>
        <li>{{ t('spotify.setupStep3') }}</li>
      </ol>
      <div class="flex gap-2">
        <input
          v-model="clientIdInput"
          type="text"
          :placeholder="t('spotify.clientIdPlaceholder')"
          class="flex-1 rounded-lg border border-border bg-panel px-2 py-1.5 text-xs text-text"
        />
        <button
          type="button"
          class="accent-gradient rounded-lg px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
          :disabled="busy === 'config'"
          @click="saveClientId"
        >
          {{ t('common.save') }}
        </button>
      </div>
      <p class="text-[11px] text-text-dim">{{ t('spotify.storageNote') }}</p>
    </div>

    <!-- 2. Connect -->
    <div v-else-if="!status?.connected" class="mt-4 flex flex-wrap items-center gap-2">
      <button
        type="button"
        class="accent-gradient rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        :disabled="busy === 'login'"
        @click="connect"
      >
        {{ busy === 'login' ? t('spotify.opening') : t('spotify.connect') }}
      </button>
      <button type="button" class="text-xs text-text-dim hover:text-text" @click="status = null; refresh()">
        {{ t('spotify.changeClientId') }}
      </button>
      <p class="w-full text-[11px] text-text-dim">{{ t('spotify.fiveUserNote') }}</p>
    </div>

    <!-- 3. Listening -> match -> build -->
    <template v-else>
      <div class="mt-4 flex flex-wrap items-end gap-2">
        <label class="text-xs text-text-dim">
          {{ t('spotify.sourceLabel') }}
          <select
            v-model="source"
            class="ml-1 rounded border border-border bg-panel-2 px-2 py-1 text-xs text-text"
          >
            <option value="recent">{{ t('spotify.sourceRecent') }}</option>
            <option value="top_short">{{ t('spotify.sourceTopShort') }}</option>
            <option value="top_medium">{{ t('spotify.sourceTopMedium') }}</option>
            <option value="top_long">{{ t('spotify.sourceTopLong') }}</option>
            <option value="saved">{{ t('spotify.sourceSaved') }}</option>
          </select>
        </label>
        <button
          type="button"
          class="rounded-lg border border-border px-3 py-1.5 text-xs text-text disabled:opacity-50"
          :disabled="!!busy"
          @click="loadListening"
        >
          {{ busy === 'listening' ? t('common.loading') : t('spotify.load') }}
        </button>
        <span v-if="tracks.length" class="text-xs text-text-dim">
          {{ t('spotify.trackCount', { n: tracks.length }) }}
        </span>
      </div>

      <p
        v-if="tracks.length && genreStatus !== 'ok'"
        class="mt-2 rounded-lg border border-status-failed/30 bg-status-failed/10 p-2 text-[11px] text-status-failed"
      >
        {{ genreStatus === 'unavailable' ? t('spotify.genresUnavailable') : t('spotify.genresPartial') }}
      </p>

      <!-- match -->
      <div v-if="tracks.length" class="mt-4 space-y-2 border-t border-border pt-3">
        <p class="text-xs font-medium text-text">{{ t('spotify.matchTitle') }}</p>
        <p class="text-[11px] text-text-dim">{{ t('spotify.matchNote') }}</p>
        <div class="flex flex-wrap items-center gap-2">
          <textarea
            v-model="folder"
            rows="3"
            :placeholder="t('spotify.folderPlaceholder')"
            class="min-w-0 flex-1 rounded-lg border border-border bg-panel-2 px-2 py-1.5 font-mono text-[11px] text-text"
          ></textarea>
          <label class="flex items-center gap-1 text-[11px] text-text-dim">
            <input v-model="includeLibrary" type="checkbox" class="accent-accent1" />
            {{ t('spotify.includeLibrary') }}
          </label>
          <button
            type="button"
            class="rounded-lg border border-border px-3 py-1.5 text-xs text-text disabled:opacity-50"
            :disabled="!!busy"
            @click="matchLocal"
          >
            {{ busy === 'match' ? t('spotify.matching') : t('spotify.match') }}
          </button>
        </div>
      </div>

      <div v-if="matched.length || missing.length" class="mt-4 grid gap-3 md:grid-cols-2">
        <div>
          <p class="text-xs font-medium text-status-done">
            {{ t('spotify.matchedCount', { n: matched.length }) }}
          </p>
          <ul class="mt-1 max-h-56 space-y-1 overflow-y-auto">
            <li
              v-for="(track, index) in matched"
              :key="track.track_id"
              class="rounded border border-border/60 bg-panel-2 px-2 py-1 text-[11px]"
            >
              <div class="flex items-start gap-1">
                <div class="min-w-0 flex-1">
                  <p class="truncate text-text">{{ track.artist }}: {{ track.title }}</p>
                  <p class="truncate text-text-dim">
                    {{ track.genres.length ? track.genres.join(', ') : t('spotify.noGenres') }}
                    <span v-if="track.year"> · {{ track.year }}</span>
                  </p>
                  <p class="truncate text-text-dim/70">{{ track.local.label }} · {{ track.score }}</p>
                </div>
                <button
                  type="button"
                  class="shrink-0 text-text-dim hover:text-status-failed"
                  :title="t('spotify.drop')"
                  @click="dropMatch(index)"
                >✕</button>
              </div>
            </li>
          </ul>
        </div>

        <div>
          <p class="text-xs font-medium text-text-dim">
            {{ t('spotify.missingCount', { n: missing.length }) }}
          </p>
          <p class="text-[11px] text-text-dim/70">{{ t('spotify.missingNote') }}</p>
          <ul class="mt-1 max-h-56 space-y-1 overflow-y-auto">
            <li
              v-for="track in missing"
              :key="track.track_id"
              class="truncate rounded border border-border/40 px-2 py-1 text-[11px] text-text-dim"
            >
              {{ track.artist }}: {{ track.title }}
            </li>
          </ul>
        </div>
      </div>

      <!-- build -->
      <div v-if="matched.length" class="mt-4 space-y-2 border-t border-border pt-3">
        <label class="block text-xs text-text-dim">
          {{ t('spotify.styleTags') }}
          <input
            v-model="styleTags"
            type="text"
            :placeholder="t('spotify.styleTagsPlaceholder')"
            class="mt-1 w-full rounded border border-border bg-panel-2 px-2 py-1.5 text-xs text-text"
          />
        </label>
        <p class="text-[11px] text-text-dim">{{ t('spotify.styleTagsNote') }}</p>

        <div class="flex flex-wrap items-end gap-2">
          <label class="text-xs text-text-dim">
            {{ t('spotify.datasetName') }}
            <input
              v-model="datasetName"
              type="text"
              class="ml-1 rounded border border-border bg-panel-2 px-2 py-1 text-xs text-text"
            />
          </label>
          <label class="text-xs text-text-dim">
            {{ t('spotify.trigger') }}
            <input
              v-model="trigger"
              type="text"
              :placeholder="t('spotify.triggerPlaceholder')"
              class="ml-1 w-32 rounded border border-border bg-panel-2 px-2 py-1 text-xs text-text"
            />
          </label>
          <label class="flex items-center gap-1 text-[11px] text-text-dim">
            <input v-model="includeArtist" type="checkbox" class="accent-accent1" />
            {{ t('spotify.includeArtist') }}
          </label>
          <button
            type="button"
            class="accent-gradient rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            :disabled="!!busy"
            @click="build"
          >
            {{ busy === 'build' ? t('spotify.building') : t('spotify.build') }}
          </button>
        </div>
        <p class="text-[11px] text-text-dim">{{ t('spotify.triggerNote') }}</p>
      </div>

      <div v-if="built.length" class="mt-3 rounded-lg border border-status-done/30 bg-status-done/10 p-3">
        <p class="text-xs font-medium text-status-done">{{ t('spotify.builtCount', { n: built.length }) }}</p>
        <ul class="mt-1 max-h-40 space-y-0.5 overflow-y-auto text-[11px] text-text-dim">
          <li v-for="file in built" :key="file.file" class="truncate">
            {{ file.file }} → <span class="text-text">{{ file.caption }}</span>
          </li>
        </ul>
      </div>
    </template>

    <p v-if="error" class="mt-3 whitespace-pre-line rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">
      {{ error }}
    </p>
  </section>
</template>
