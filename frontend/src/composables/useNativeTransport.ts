/**
 * Plays the timeline through the native engine instead of Web Audio.
 *
 * The switch is the device, not a setting: if the engine has an output
 * open, it owns playback, and if it does not, the browser path runs exactly
 * as before. That keeps one obvious rule - "the audio engine panel says
 * open, so that is what you are hearing" - and means the DAW still works on
 * a machine where the engine was never built.
 *
 * Position comes back from the engine rather than being predicted here. The
 * old browser path ran its own clock from AudioContext.currentTime and that
 * was right, because it *was* the clock. The engine's playhead is a count
 * of frames actually rendered to the device, so anything this side computes
 * is a guess that drifts against the thing making the sound.
 */
import { ref } from 'vue'
import { apiFetch, apiJson } from '../api/http'
import type { TimelineProject } from '../audio/timelineTypes'

export interface NativeState {
  open: boolean
  running: boolean
  latencyMs: number
  xruns: number
}

interface EngineStatus {
  running?: boolean
  open?: boolean
  position?: number
  playing?: boolean
  latencyMs?: number
  xruns?: number
}

// The engine is polled rather than pushing, because the control socket is
// request/response and a playhead at 20Hz is smooth enough to follow by eye
// while costing a fiftieth of what a per-frame round trip would.
const POSITION_POLL_MS = 50

export function useNativeTransport() {
  const available = ref(false)
  const latencyMs = ref(0)
  const xruns = ref(0)
  const skipped = ref<string[]>([])

  let poll: ReturnType<typeof setInterval> | null = null

  /** True when the engine has an output open and should own playback. */
  async function refresh(): Promise<boolean> {
    try {
      const status = await apiFetch<EngineStatus>('/api/engine/status')
      available.value = Boolean(status.running && status.open)
      latencyMs.value = status.latencyMs ?? 0
      xruns.value = status.xruns ?? 0
    } catch {
      available.value = false
    }
    return available.value
  }

  /** Pushes the arrangement across. Returns what could not be played. */
  async function sync(project: TimelineProject): Promise<string[]> {
    const result = await apiJson<{ skipped: string[] }>('/api/engine/sync', { project })
    skipped.value = result.skipped ?? []
    return skipped.value
  }

  async function play(project: TimelineProject, fromSec: number, onPosition: (sec: number) => void,
                      onEnded: () => void, endsAtSec: number) {
    await sync(project)
    await apiJson('/api/engine/transport', { action: 'seek', seconds: fromSec })
    await apiJson('/api/engine/transport', { action: 'play' })

    stopPolling()
    poll = setInterval(async () => {
      try {
        const status = await apiFetch<EngineStatus>('/api/engine/status')
        const position = status.position ?? 0
        xruns.value = status.xruns ?? 0
        onPosition(position)
        // The engine has no idea where the song ends - it renders silence
        // past the last clip quite happily - so the end is this side's
        // business, exactly as it is for a loop point.
        if (endsAtSec > 0 && position >= endsAtSec) {
          await stop()
          onEnded()
        }
      } catch {
        // A dropped engine should not leave the playhead crawling forever.
        stopPolling()
        onEnded()
      }
    }, POSITION_POLL_MS)
  }

  async function stop() {
    stopPolling()
    try {
      await apiJson('/api/engine/transport', { action: 'stop' })
    } catch {
      // Already gone; nothing to stop.
    }
  }

  async function seek(seconds: number) {
    try {
      await apiJson('/api/engine/transport', { action: 'seek', seconds })
    } catch {
      // The transport call that follows will report the real problem.
    }
  }

  function stopPolling() {
    if (poll) clearInterval(poll)
    poll = null
  }

  return { available, latencyMs, xruns, skipped, refresh, sync, play, stop, seek, stopPolling }
}
