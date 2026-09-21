<script setup lang="ts">
/**
 * Treblo, as part of the studio rather than a tab bolted onto it.
 *
 * Treblo publishes a real developer API (Melodia), so this is an ordinary
 * integration: the user brings their own key, and the free tier is 1,500
 * credits against 100 per song - fifteen songs before anything is owed.
 *
 * What makes it seamless is the last step, not the first: a finished song is
 * downloaded into the library by the backend, so it appears in the DAW, the
 * separation lab and the LoRA dataset builder immediately. Nothing is
 * exported, re-imported or dragged between folders.
 *
 * Treblo's own advice is to send a prompt and nothing else - it infers tags,
 * lyrics and strength settings, and overriding them measurably hurts the
 * result. The extra controls are therefore behind a disclosure and unset by
 * default, rather than a wall of sliders inviting you to make it worse.
 */
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import PageFrame from '../../components/shared/PageFrame.vue'
import { useRouter } from 'vue-router'
import { apiFetch, apiJson } from '../../api/http'
import { listTracks, type SavedTrack } from '../../api/tracks'

const { t } = useI18n()
const router = useRouter()

interface TrebloStatus {
  configured: boolean
  key_hint: string | null
  credits_per_song: number
}

interface Song {
  taskId: string
  title: string
  status: 'pending' | 'done' | 'failed'
  audioUrl: string | null
  trackId: number | null
  error?: string
}

const status = ref<TrebloStatus | null>(null)
const keyInput = ref('')
const prompt = ref('')
const lyrics = ref('')
const instrumental = ref(false)
const outputFormat = ref<'mp3' | 'wav' | 'flac' | 'ogg' | 'm4a'>('mp3')
const advanced = ref(false)
const lengthMin = ref(0)
const lengthMax = ref(150)
const negativeTags = ref('')
const styleScale = ref(4.5)

const busy = ref(false)
const error = ref('')
const songs = ref<Song[]>([])

const timers = new Set<number>()

const canGenerate = computed(
  () => !!status.value?.configured && !busy.value && (prompt.value.trim() || lyrics.value.trim()),
)

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

async function refresh() {
  try {
    status.value = await apiFetch<TrebloStatus>('/api/treblo/status')
  } catch (err) {
    error.value = message(err)
  }
}

async function saveKey() {
  const value = keyInput.value.trim()
  if (!value) return
  error.value = ''
  try {
    status.value = await apiJson<TrebloStatus>('/api/treblo/key', { api_key: value })
    keyInput.value = ''
  } catch (err) {
    error.value = message(err)
  }
}

async function clearKey() {
  try {
    status.value = await apiFetch<TrebloStatus>('/api/treblo/key', { method: 'DELETE' })
  } catch (err) {
    error.value = message(err)
  }
}

async function generate() {
  if (!canGenerate.value) return
  busy.value = true
  error.value = ''
  try {
    const body: Record<string, unknown> = {
      prompt: prompt.value.trim(),
      instrumental: instrumental.value,
      output_format: outputFormat.value,
    }
    if (!instrumental.value && lyrics.value.trim()) body.lyrics = lyrics.value.trim()
    if (advanced.value) {
      // Both bounds must be multiples of 30 per the API; the inputs step in
      // thirties so this cannot send something it will reject.
      if (lengthMax.value > lengthMin.value) body.length_range = [lengthMin.value, lengthMax.value]
      const negatives = negativeTags.value.split(',').map((tag) => tag.trim()).filter(Boolean)
      if (negatives.length) body.negative_tags = negatives
      body.style_scale = styleScale.value
    }

    const started = await apiJson<{ task_id: string }>('/api/treblo/generate', body)
    const song: Song = {
      taskId: started.task_id,
      title: (prompt.value.trim() || lyrics.value.trim()).slice(0, 60) || 'Treblo song',
      status: 'pending',
      audioUrl: null,
      trackId: null,
    }
    songs.value.unshift(song)
    poll(song)
  } catch (err) {
    error.value = message(err)
  } finally {
    busy.value = false
  }
}

/**
 * Everything Treblo has ever made on this machine.
 *
 * The songs list above is this session only - close the tab and it is gone,
 * and hearing yesterday's track meant going to Treblo's own dashboard and
 * digging through generation history. Every finished song is already
 * imported into the library though, so the list is right here; it just was
 * never shown.
 */
const past = ref<SavedTrack[]>([])

async function loadPast() {
  try {
    past.value = await listTracks('treblo')
  } catch {
    // The library being unreachable is already loud elsewhere, and an
    // empty history should not stop anyone generating something new.
  }
}

void loadPast()

/** Treblo quotes about fifteen seconds to first audio, so a five-second
 *  poll is responsive without hammering a metered API. */
function poll(song: Song, attempt = 0) {
  if (attempt > 180) {
    song.status = 'failed'
    song.error = t('treblo.timedOut')
    return
  }
  const timer = window.setTimeout(async () => {
    timers.delete(timer)
    try {
      const result = await apiFetch<{ status: string }>(`/api/treblo/status/${song.taskId}`)
      const state = (result.status || '').toUpperCase()
      if (state === 'SUCCESS') {
        await collect(song)
        return
      }
      if (state === 'FAILURE') {
        song.status = 'failed'
        song.error = t('treblo.generationFailed')
        return
      }
      poll(song, attempt + 1)
    } catch (err) {
      song.status = 'failed'
      song.error = message(err)
    }
  }, 5000)
  timers.add(timer)
}

/** Fetch the URL, then have the backend pull it into the library - the step
 *  that makes it part of the studio instead of a link. */
async function collect(song: Song) {
  try {
    const result = await apiFetch<{ song_paths?: string[] }>(`/api/treblo/result/${song.taskId}`)
    const url = result.song_paths?.[0]
    if (!url) {
      song.status = 'failed'
      song.error = t('treblo.noAudio')
      return
    }
    song.audioUrl = url
    const imported = await apiJson<{ track_id: number; audio_url: string }>('/api/treblo/import', {
      url,
      title: song.title,
      task_id: song.taskId,
      lyrics: instrumental.value ? '' : lyrics.value,
    })
    song.trackId = imported.track_id
    song.audioUrl = imported.audio_url
    void loadPast()
    song.status = 'done'
  } catch (err) {
    song.status = 'failed'
    song.error = message(err)
  }
}

onBeforeUnmount(() => {
  for (const timer of timers) window.clearTimeout(timer)
})

void refresh()
</script>

<template>
  <PageFrame :title="t('treblo.title')" :subtitle="t('treblo.subtitle')" accent="#f472b6">


    <!-- Key -->
    <section v-if="!status?.configured" class="card space-y-2 p-4">
      <p class="text-xs font-medium text-text">{{ t('treblo.setupTitle') }}</p>
      <ol class="list-decimal space-y-1 pl-4 text-xs text-text-dim">
        <li>{{ t('treblo.setupStep1') }}</li>
        <li>{{ t('treblo.setupStep2') }}</li>
      </ol>
      <div class="flex gap-2">
        <input
          v-model="keyInput"
          type="password"
          :placeholder="t('treblo.keyPlaceholder')"
          class="min-w-0 flex-1 rounded border border-border bg-panel-2 px-2 py-1.5 text-xs text-text"
          @keyup.enter="saveKey"
        />
        <button type="button" class="accent-gradient rounded px-3 py-1.5 text-xs" @click="saveKey">
          {{ t('common.save') }}
        </button>
      </div>
      <p class="text-[11px] text-text-faint">{{ t('treblo.keyNote') }}</p>
    </section>

    <!-- Compose -->
    <section v-else class="card space-y-3 p-4">
      <div class="flex items-center gap-2">
        <span class="rounded-full bg-status-done/20 px-2 py-0.5 text-[11px] text-status-done">
          {{ t('treblo.connected', { hint: status.key_hint }) }}
        </span>
        <span class="text-[11px] text-text-faint">
          {{ t('treblo.creditCost', { n: status.credits_per_song }) }}
        </span>
        <button type="button" class="ml-auto text-[11px] text-text-dim hover:text-text" @click="clearKey">
          {{ t('treblo.forgetKey') }}
        </button>
      </div>

      <label class="block text-sm text-text-dim">
        {{ t('treblo.prompt') }}
        <textarea
          v-model="prompt"
          rows="2"
          :placeholder="t('treblo.promptPlaceholder')"
          class="mt-1 w-full rounded-lg border border-border bg-panel-2 p-2.5 text-sm text-text"
        ></textarea>
      </label>
      <p class="text-[11px] text-text-faint">{{ t('treblo.promptHint') }}</p>

      <label v-if="!instrumental" class="block text-xs text-text-dim">
        {{ t('treblo.lyrics') }}
        <textarea
          v-model="lyrics"
          rows="3"
          :placeholder="t('treblo.lyricsPlaceholder')"
          class="mt-1 w-full rounded-lg border border-border bg-panel-2 p-2 text-xs text-text"
        ></textarea>
      </label>

      <div class="flex flex-wrap items-center gap-3">
        <label class="flex items-center gap-1.5 text-xs text-text-dim">
          <input v-model="instrumental" type="checkbox" class="accent-accent1" />
          {{ t('treblo.instrumental') }}
        </label>
        <label class="flex items-center gap-1 text-xs text-text-dim">
          {{ t('treblo.format') }}
          <select v-model="outputFormat" class="rounded border border-border bg-panel-2 px-1.5 py-1 text-xs text-text">
            <option value="mp3">MP3</option>
            <option value="wav">WAV</option>
            <option value="flac">FLAC</option>
            <option value="ogg">OGG</option>
            <option value="m4a">M4A</option>
          </select>
        </label>
        <button
          type="button"
          class="text-[11px] text-text-dim underline-offset-2 hover:text-text hover:underline"
          @click="advanced = !advanced"
        >{{ advanced ? t('treblo.hideAdvanced') : t('treblo.showAdvanced') }}</button>

        <button
          type="button"
          class="accent-gradient ml-auto rounded-lg px-5 py-2 text-sm disabled:opacity-40"
          :disabled="!canGenerate"
          @click="generate"
        >{{ busy ? t('treblo.starting') : t('treblo.generate') }}</button>
      </div>

      <div v-if="advanced" class="grid gap-3 border-t border-border pt-3 sm:grid-cols-2">
        <p class="sm:col-span-2 text-[11px] text-text-faint">{{ t('treblo.advancedWarning') }}</p>
        <label class="text-xs text-text-dim">
          {{ t('treblo.lengthRange', { min: lengthMin, max: lengthMax }) }}
          <div class="mt-1 flex gap-2">
            <input v-model.number="lengthMin" type="range" min="0" max="270" step="30" class="min-w-0 flex-1" />
            <input v-model.number="lengthMax" type="range" min="30" max="300" step="30" class="min-w-0 flex-1" />
          </div>
        </label>
        <label class="text-xs text-text-dim">
          {{ t('treblo.styleScale', { n: styleScale.toFixed(1) }) }}
          <input v-model.number="styleScale" type="range" min="1" max="12" step="0.5" class="mt-1 w-full" />
        </label>
        <label class="text-xs text-text-dim sm:col-span-2">
          {{ t('treblo.negativeTags') }}
          <input
            v-model="negativeTags"
            type="text"
            :placeholder="t('treblo.negativePlaceholder')"
            class="mt-1 w-full rounded border border-border bg-panel-2 px-2 py-1.5 text-xs text-text"
          />
        </label>
      </div>

      <p v-if="error" class="rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">{{ error }}</p>
    </section>

    <!-- Results -->
    <section v-if="songs.length" class="card p-4">
      <h2 class="text-sm font-semibold text-text">{{ t('treblo.songs') }}</h2>
      <ul class="mt-2 space-y-2">
        <li v-for="song in songs" :key="song.taskId" class="rounded-lg border border-border bg-panel-2/50 p-2.5">
          <div class="flex flex-wrap items-center gap-2">
            <span
              class="h-2 w-2 shrink-0 rounded-full"
              :class="song.status === 'done' ? 'bg-status-done'
                : song.status === 'failed' ? 'bg-status-failed' : 'bg-status-queued breathe'"
            ></span>
            <span class="min-w-0 flex-1 truncate text-sm text-text">{{ song.title }}</span>
            <span v-if="song.status === 'pending'" class="text-[11px] text-text-faint">
              {{ t('treblo.generating') }}
            </span>
            <button
              v-if="song.trackId"
              type="button"
              class="rounded border border-accent1/50 bg-accent1/10 px-2 py-1 text-[11px] text-text"
              @click="router.push('/editor/new')"
            >{{ t('treblo.openInDaw') }}</button>
          </div>
          <audio v-if="song.audioUrl && song.status === 'done'" :src="song.audioUrl" controls class="mt-2 w-full"></audio>
          <p v-if="song.error" class="mt-1 text-[11px] text-status-failed">{{ song.error }}</p>
          <p v-if="song.status === 'done'" class="mt-1 text-[11px] text-text-faint">
            {{ t('treblo.inLibrary') }}
          </p>
        </li>
      </ul>
    </section>

    <!-- Everything from before this session -->
    <section v-if="past.length" class="card p-4">
      <h2 class="mb-2 text-sm font-semibold text-text">{{ t('treblo.history') }}</h2>
      <p class="mb-2 text-xs text-text-dim">{{ t('treblo.historyHint') }}</p>
      <ul class="space-y-2">
        <li v-for="item in past" :key="item.id" class="rounded-lg border border-border bg-panel-2/40 p-2">
          <div class="flex items-center gap-2">
            <span class="min-w-0 flex-1 truncate text-xs text-text">{{ item.title }}</span>
            <span class="shrink-0 text-[10px] text-text-faint">
              {{ new Date(item.created_at).toLocaleDateString() }}
            </span>
            <a
              :href="item.audio_url"
              :download="`${item.title}.mp3`"
              class="shrink-0 rounded border border-border px-2 py-0.5 text-[10px] text-text-dim hover:text-text"
            >{{ t('common.download') }}</a>
          </div>
          <audio :src="item.audio_url" controls preload="none" class="mt-1.5 w-full"></audio>
        </li>
      </ul>
    </section>
  </PageFrame>
</template>
