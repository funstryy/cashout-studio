<script setup lang="ts">
/**
 * The startup animation.
 *
 * Plays once per launch, over everything, and gets out of the way. Three
 * rules shaped it, all learned from splash screens that are hated:
 *
 *   1. It must never trap anyone. Click, any key, or the Skip button ends
 *      it, and if the video fails to load, errors, or simply never fires
 *      `ended`, a watchdog dismisses it anyway. A splash that can hang is a
 *      program that can't be opened.
 *   2. It must be refusable. "Don't show this again" is right there, and is
 *      remembered. Five seconds is nothing once and tiresome on the two
 *      hundredth launch.
 *   3. It must not be the reason the app is silent-blocked. Autoplay with
 *      sound is refused by the engine unless the user has interacted with
 *      the page, and a rejected play() would leave a frozen first frame. So
 *      it tries with sound and immediately retries muted if that is refused.
 *
 * The clip is H.264, 5.5 MB, and it used to be HEVC at 2.9.
 *
 * The smaller file was the wrong call and this is the note explaining why,
 * because the reasoning looked sound at the time. Chromium only decodes
 * H.265 where the platform supplies a decoder, which on Windows means the
 * Store's HEVC Video Extensions. Where that is missing the source never
 * loads, the watchdog below trips, and the app opens with no splash at all
 * - silently, which is the worst possible failure for something whose only
 * job is to be seen. That is exactly what happened on the machine this was
 * built for. Two and a half megabytes is not worth an animation nobody
 * ever sees.
 *
 * Do not gate this on canPlayType() either. Measured: canPlayType for the
 * old HEVC file returned "" - the answer for "cannot play" - on hardware
 * that decoded it perfectly. The honest test is to try loading it and
 * listen for the events, which is what happens below.
 */
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiFetch } from '../../api/http'

const emit = defineEmits<{ done: [] }>()
const { t } = useI18n()

const DISABLE_KEY = 'cashout_intro_disabled'
const POSTER = '/intro/cashout-intro-poster.jpg'

const SRC = '/intro/cashout-intro.mp4'

/** Belt and braces against a video that never ends: the clip is 5.8s, so
 *  this only ever fires when something has genuinely gone wrong. */
const WATCHDOG_MS = 9000

const video = ref<HTMLVideoElement | null>(null)
const leaving = ref(false)
const muted = ref(false)
const visible = ref(false)
let watchdog: number | undefined

function disabled(): boolean {
  try {
    return localStorage.getItem(DISABLE_KEY) === '1'
  } catch {
    return false
  }
}

function finish() {
  if (leaving.value) return
  leaving.value = true
  window.clearTimeout(watchdog)
  try {
    video.value?.pause()
  } catch {
    // Already gone; nothing to stop.
  }
  // Matches the CSS fade, so the app is revealed under a dissolving overlay
  // rather than appearing after a black gap.
  window.setTimeout(() => emit('done'), 320)
}

function neverAgain() {
  try {
    localStorage.setItem(DISABLE_KEY, '1')
  } catch {
    // Private browsing - it will simply ask again next time.
  }
  finish()
}

function onKey(event: KeyboardEvent) {
  // Any key. Someone reaching for the keyboard during a splash wants past it,
  // and making them find the right one is the joke wearing thin.
  event.preventDefault()
  finish()
}

/** Resolves once there are frames to show, or false if there never will be. */
function waitForData(element: HTMLVideoElement): Promise<boolean> {
  if (element.readyState >= 2) return Promise.resolve(true)
  return new Promise((resolve) => {
    const ok = () => { cleanup(); resolve(true) }
    const bad = () => { cleanup(); resolve(false) }
    const timer = window.setTimeout(bad, 5000)
    function cleanup() {
      window.clearTimeout(timer)
      element.removeEventListener('loadeddata', ok)
      element.removeEventListener('error', bad)
    }
    element.addEventListener('loadeddata', ok, { once: true })
    element.addEventListener('error', bad, { once: true })
  })
}

async function attemptPlay() {
  const element = video.value
  if (!element) return finish()

  // Wait for frames before calling play(). Measured, not guessed: calling
  // play() straight out of onMounted rejected with "no supported source"
  // because the resource had not been selected yet - readyState was still 0 -
  // and the muted retry rejected for the same reason. The splash dismissed
  // itself about 40ms after mounting, on a machine that decodes the file
  // perfectly well.
  if (!(await waitForData(element))) return finish()

  try {
    await element.play()
  } catch {
    // Refused - the autoplay-with-sound policy. Muted autoplay is always
    // permitted, so this second attempt is the one that actually runs.
    muted.value = true
    element.muted = true
    try {
      await element.play()
    } catch {
      finish()
    }
  }
}

/*
 * The boot readout.
 *
 * A splash that is only a video is a loading screen with a logo on it, and
 * the thing that makes a piece of equipment feel like equipment at power-on
 * is that it tells you what it found. So these are real probes against the
 * real endpoints, printed as they answer - not a scripted list of
 * reassuring lines on a timer. If the backend is down the readout says so
 * and the user learns it here rather than from an empty page three clicks
 * later.
 *
 * None of it gates the splash. The watchdog and the video's own `ended` are
 * still what dismiss it, because a slow probe must never hold the app shut.
 */
type Check = { key: string; label: string; value: string; ok: boolean | null }

const checks = ref<Check[]>([
  { key: 'system', label: 'intro.bootSystem', value: '', ok: null },
  { key: 'engine', label: 'intro.bootEngine', value: '', ok: null },
  { key: 'library', label: 'intro.bootLibrary', value: '', ok: null },
])

function settle(key: string, ok: boolean, value: string) {
  const row = checks.value.find((c) => c.key === key)
  if (row) {
    row.ok = ok
    row.value = value
  }
}

async function probe() {
  // In parallel, and each one owns its own failure: one dead endpoint
  // should not blank the other two lines.
  await Promise.allSettled([
    apiFetch<{ gpu?: { name?: string } }>('/api/system')
      .then((r) => settle('system', true, r.gpu?.name || 'ok'))
      .catch(() => settle('system', false, t('intro.bootUnreachable'))),

    apiFetch<{ available: boolean; open: boolean; sampleRate: number; latencyMs: number }>(
      '/api/engine/status',
    )
      .then((r) => {
        if (!r.available) return settle('engine', false, t('intro.bootEngineMissing'))
        settle(
          'engine',
          true,
          r.open
            ? t('intro.bootEngineOpen', {
                rate: r.sampleRate,
                ms: r.latencyMs.toFixed(1),
              })
            : t('intro.bootEngineReady'),
        )
      })
      .catch(() => settle('engine', false, t('intro.bootUnreachable'))),

    apiFetch<{ data: unknown[] }>('/api/tracks')
      .then((r) => settle('library', true, t('intro.bootTracks', { n: r.data.length })))
      .catch(() => settle('library', false, t('intro.bootUnreachable'))),
  ])
}

onMounted(async () => {
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  if (disabled() || reduced) {
    emit('done')
    return
  }
  visible.value = true
  // The <video> is behind `v-if="visible"`, so it does not exist yet: Vue
  // flushes the render on the next tick. Without this wait, `video.value`
  // is null when attemptPlay() reads it, attemptPlay bails to finish(), and
  // the splash dismisses itself about 300ms after mounting - which is
  // exactly what it was doing, silently, on a machine whose browser plays
  // the clip perfectly. The earlier readyState fix was real but it was not
  // the whole fault; this is the rest of it.
  await nextTick()
  window.addEventListener('keydown', onKey)
  watchdog = window.setTimeout(finish, WATCHDOG_MS)
  // Deliberately not awaited: the probes run alongside the clip, so the
  // readout fills in while it plays instead of delaying it.
  void probe()
  await attemptPlay()
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.clearTimeout(watchdog)
})
</script>

<template>
  <div
    v-if="visible"
    class="fixed inset-0 z-[200] flex items-center justify-center bg-black transition-opacity duration-300"
    :class="leaving ? 'pointer-events-none opacity-0' : 'opacity-100'"
    @click="finish"
  >
    <video
      ref="video"
      :src="SRC"
      :poster="POSTER"
      class="h-full w-full object-cover"
      playsinline
      preload="auto"
      @ended="finish"
      @error="finish"
    >
    </video>

    <!-- Vignette. The clip is a bright animation on a black field, and
         without this its edges meet the bezel dead flat - which is what
         made it read as a video embedded in a page rather than as the
         machine coming up. -->
    <div
      class="pointer-events-none absolute inset-0"
      style="background: radial-gradient(120% 90% at 50% 45%, transparent 45%, rgba(0,0,0,0.75) 100%)"
    ></div>

    <!-- What the machine found on the way up. -->
    <div class="pointer-events-none absolute bottom-6 left-6 space-y-1 font-mono text-[10px]">
      <div v-for="row in checks" :key="row.key" class="flex items-center gap-2">
        <span
          class="led h-[5px] w-[5px] shrink-0 transition-colors"
          :style="{
            background: row.ok === null ? '#1a2430' : row.ok ? 'var(--color-status-done)' : 'var(--color-status-failed)',
            color: row.ok === null ? 'transparent' : row.ok ? 'var(--color-status-done)' : 'var(--color-status-failed)',
          }"
        ></span>
        <span class="w-24 uppercase tracking-[0.14em] text-white/35">{{ t(row.label) }}</span>
        <span :class="row.ok === false ? 'text-status-failed' : 'text-white/65'">
          {{ row.value || t('intro.bootChecking') }}
        </span>
      </div>
    </div>

    <!-- Controls sit above the video and stop the click from double-firing
         through to the overlay's own dismiss handler. -->
    <div class="absolute bottom-6 right-6 flex items-center gap-3" @click.stop>
      <button
        type="button"
        class="text-[11px] text-white/45 transition-colors hover:text-white/80"
        @click="neverAgain"
      >
        {{ t('intro.neverAgain') }}
      </button>
      <button
        type="button"
        class="key px-4 py-1.5 text-xs"
        @click="finish"
      >
        {{ t('intro.skip') }}
      </button>
    </div>

    <p
      v-if="muted"
      class="absolute left-6 top-6 text-[11px] text-white/35"
    >
      {{ t('intro.muted') }}
    </p>
  </div>
</template>
