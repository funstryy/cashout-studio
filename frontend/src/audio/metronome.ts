/**
 * Click track for the transport.
 *
 * Beats are scheduled ahead on the audio clock rather than fired from a
 * timer: setInterval drifts by tens of milliseconds under load, which is
 * audible as a wobbling click and useless for playing along to.
 */
export interface MetronomeHandle {
  stop(): void
}

const LOOK_AHEAD_SEC = 0.2
const TICK_MS = 50
const ACCENT_HZ = 1760
const BEAT_HZ = 880

function scheduleClick(ctx: BaseAudioContext, destination: AudioNode, at: number, accent: boolean): void {
  const osc = ctx.createOscillator()
  const gain = ctx.createGain()
  osc.frequency.value = accent ? ACCENT_HZ : BEAT_HZ
  // A short exponential decay reads as a click; a raw gate would pop.
  gain.gain.setValueAtTime(accent ? 0.5 : 0.32, at)
  gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.05)
  osc.connect(gain).connect(destination)
  osc.start(at)
  osc.stop(at + 0.06)
}

/**
 * Clicks from `startSec` on the project timeline, where `ctxStartTime` is the
 * audio-clock time that position was reached. `beatsPerBar` accents beat one.
 */
export function startMetronome(
  ctx: AudioContext,
  destination: AudioNode,
  bpm: number,
  startSec: number,
  ctxStartTime: number,
  beatsPerBar = 4,
): MetronomeHandle {
  const secondsPerBeat = 60 / Math.max(1, bpm)
  let nextBeat = Math.ceil(startSec / secondsPerBeat)

  const timer = window.setInterval(() => {
    const horizon = ctx.currentTime + LOOK_AHEAD_SEC
    while (ctxStartTime + nextBeat * secondsPerBeat - startSec < horizon) {
      const at = ctxStartTime + nextBeat * secondsPerBeat - startSec
      if (at >= ctx.currentTime) {
        scheduleClick(ctx, destination, at, nextBeat % beatsPerBar === 0)
      }
      nextBeat += 1
    }
  }, TICK_MS)

  return {
    stop() {
      window.clearInterval(timer)
    },
  }
}
