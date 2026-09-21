/**
 * The channel rack: a step sequencer over one-shot samples.
 *
 * The timeline answers "what plays at 1:24"; the rack answers "what is the
 * pattern". They are different questions and a DAW needs both - laying a
 * hi-hat out as sixteen dragged clips is not the same work as clicking
 * sixteen squares.
 *
 * Steps are scheduled ahead on the audio clock, for the same reason the
 * metronome is: a timer that fires the hits directly drifts by tens of
 * milliseconds under load, which at a sixteenth-note grid is the difference
 * between a groove and a stumble.
 *
 * The graph is built from the same buildChannel() the mixer uses, so a
 * channel's volume, pan, EQ and reverb behave exactly as they do on a track,
 * and a bounced pattern sounds like what was heard.
 */
import {
  applyChannelSettings,
  buildChannel,
  defaultChannelSettings,
  getReverbImpulse,
} from './mixerEngine'
import type { BuiltChannel, ChannelSettings, MasterSettings } from './mixerEngine'
import { MIDDLE_C, channelMode, channelNotes } from './timelineTypes'
import type { ChannelRack, RackChannel, RackNote } from './timelineTypes'

export const STEPS_PER_BAR_CHOICES = [8, 16, 32] as const
export const BAR_CHOICES = [1, 2, 4] as const

export function defaultRack(): ChannelRack {
  return { channels: [], stepsPerBar: 16, bars: 1 }
}

export function newRackChannel(name: string, sourceUrl: string, totalSteps: number): RackChannel {
  return {
    id: crypto.randomUUID(),
    name,
    sourceUrl,
    steps: new Array(totalSteps).fill(false),
    settings: defaultChannelSettings(),
    pitch: 0,
  }
}

export function rackTotalSteps(rack: ChannelRack): number {
  return Math.max(1, rack.stepsPerBar * rack.bars)
}

/** One step, in seconds. A bar is four beats, so a 16-step bar is sixteenths. */
export function stepDuration(bpm: number, stepsPerBar: number): number {
  const secondsPerBar = (60 / Math.max(1, bpm)) * 4
  return secondsPerBar / Math.max(1, stepsPerBar)
}

export function rackDuration(rack: ChannelRack, bpm: number): number {
  return rackTotalSteps(rack) * stepDuration(bpm, rack.stepsPerBar)
}

/** Steps are stored per channel, so changing the pattern length has to keep
 *  what was already programmed rather than starting everyone over. */
export function resizeChannelSteps(rack: ChannelRack): void {
  const total = rackTotalSteps(rack)
  for (const channel of rack.channels) {
    if (channel.steps.length === total) continue
    const next = new Array<boolean>(total).fill(false)
    for (let i = 0; i < Math.min(total, channel.steps.length); i++) next[i] = channel.steps[i]
    channel.steps = next
  }
}

function effectiveGain(settings: ChannelSettings, anySolo: boolean): number {
  if (settings.muted) return 0
  if (anySolo && !settings.solo) return 0
  return settings.volume
}

function playbackRate(channel: RackChannel): number {
  return Math.pow(2, (channel.pitch ?? 0) / 12)
}

/**
 * The rate for one note.
 *
 * Pitch is resampling, not time-stretching: a note an octave up plays at
 * double speed and therefore half the length. That is what a sampler does
 * and what people expect from a drum machine - an 808 pitched up should get
 * shorter and snappier, not stay the same length with a higher tone.
 */
function noteRate(channel: RackChannel, note: RackNote): number {
  return Math.pow(2, ((note.key - MIDDLE_C) + (channel.pitch ?? 0)) / 12)
}

/** Every event a channel produces in one pass of the pattern. */
interface ScheduledHit {
  /** Offset from the start of the pattern, in steps. */
  step: number
  rate: number
  gain: number
  /** Steps, or null to let the sample ring out. A note has a length; a step
   *  does not, which is the practical difference between the two modes. */
  lengthSteps: number | null
}

/**
 * What a channel plays for one pass, from whichever half of it is live.
 *
 * Steps and notes are mutually exclusive on purpose - see RackChannel. This
 * is the single place that decides which, so the live scheduler and the
 * offline render can never disagree about what a pattern contains.
 */
export function channelHits(channel: RackChannel, totalSteps: number): ScheduledHit[] {
  if (channelMode(channel) === 'notes') {
    return channelNotes(channel)
      .filter((note) => note.start < totalSteps)
      .map((note) => ({
        step: note.start,
        rate: noteRate(channel, note),
        gain: Math.max(0, Math.min(1, note.velocity)),
        lengthSteps: note.length,
      }))
  }
  const hits: ScheduledHit[] = []
  for (let index = 0; index < totalSteps; index++) {
    if (channel.steps[index]) {
      hits.push({ step: index, rate: playbackRate(channel), gain: 1, lengthSteps: null })
    }
  }
  return hits
}

interface Graph {
  master: BuiltChannel
  channels: Map<string, BuiltChannel>
}

function buildGraph(
  ctx: BaseAudioContext,
  rack: ChannelRack,
  master: MasterSettings,
  destination: AudioNode,
): Graph {
  const impulse = getReverbImpulse(ctx.sampleRate)
  const masterChannel = buildChannel(ctx, true, impulse)
  masterChannel.output.connect(destination)
  applyChannelSettings(masterChannel, master, master.volume)

  const channels = new Map<string, BuiltChannel>()
  const anySolo = rack.channels.some((c) => c.settings.solo)
  for (const channel of rack.channels) {
    const built = buildChannel(ctx, false, impulse)
    built.output.connect(masterChannel.input)
    applyChannelSettings(built, channel.settings, effectiveGain(channel.settings, anySolo))
    channels.set(channel.id, built)
  }
  return { master: masterChannel, channels }
}

export interface RackHandle {
  stop(): void
}

const MAX_TAIL_SEC = 8
const LOOK_AHEAD_SEC = 0.2
const TICK_MS = 25

/**
 * Plays the pattern on a loop until stopped.
 *
 * `onStep` reports the step being heard, not the step being scheduled - the
 * scheduler runs up to 200 ms ahead, and a playhead that ran that far ahead of
 * the sound would look broken.
 */
export function startRack(options: {
  ctx: AudioContext
  rack: ChannelRack
  master: MasterSettings
  buffers: Map<string, AudioBuffer>
  bpm: number
  onStep?: (step: number) => void
}): RackHandle {
  const { ctx, rack, master, buffers, bpm, onStep } = options
  const graph = buildGraph(ctx, rack, master, ctx.destination)
  const total = rackTotalSteps(rack)
  const stepSec = stepDuration(bpm, rack.stepsPerBar)

  const startedAt = ctx.currentTime + 0.05
  let nextStep = 0
  const live: AudioBufferSourceNode[] = []

  const timer = window.setInterval(() => {
    // Settings are re-applied every tick so moving a fader or hitting mute
    // takes effect while the pattern is running, without rebuilding the graph.
    const anySolo = rack.channels.some((c) => c.settings.solo)
    for (const channel of rack.channels) {
      const built = graph.channels.get(channel.id)
      if (built) applyChannelSettings(built, channel.settings, effectiveGain(channel.settings, anySolo))
    }
    applyChannelSettings(graph.master, master, master.volume)

    const horizon = ctx.currentTime + LOOK_AHEAD_SEC
    while (startedAt + nextStep * stepSec < horizon) {
      const at = startedAt + nextStep * stepSec
      const index = nextStep % total
      for (const channel of rack.channels) {
        const built = graph.channels.get(channel.id)
        const buffer = buffers.get(channel.sourceUrl)
        if (!built || !buffer) continue
        // Hits are recomputed per step rather than cached, so a note drawn
        // while the pattern is looping is heard on the next pass instead of
        // after a restart.
        for (const hit of channelHits(channel, total)) {
          if (Math.floor(hit.step) !== index) continue
          const source = ctx.createBufferSource()
          source.buffer = buffer
          source.playbackRate.value = hit.rate
          // Velocity rides its own gain node so it scales the note without
          // touching the channel fader the user set.
          let destination: AudioNode = built.input
          if (hit.gain < 0.999) {
            const shaper = ctx.createGain()
            shaper.gain.value = hit.gain
            shaper.connect(built.input)
            destination = shaper
          }
          source.connect(destination)
          // Fractional starts are what a nudged note is made of.
          source.start(at + (hit.step - index) * stepSec)
          if (hit.lengthSteps != null) {
            // A held note is cut at its end, with a short release so the
            // stop is not a click.
            const endsAt = at + (hit.step - index + hit.lengthSteps) * stepSec
            source.stop(endsAt + 0.02)
          }
          live.push(source)
          source.onended = () => {
            const index = live.indexOf(source)
            if (index !== -1) live.splice(index, 1)
            source.disconnect()
          }
        }
      }
      nextStep += 1
    }

    if (onStep) {
      const elapsed = ctx.currentTime - startedAt
      if (elapsed >= 0) onStep(Math.floor(elapsed / stepSec) % total)
    }
  }, TICK_MS)

  return {
    stop() {
      window.clearInterval(timer)
      for (const source of live.slice()) {
        source.onended = null
        try {
          source.stop()
        } catch {
          // already finished - fine
        }
        source.disconnect()
      }
      live.length = 0
      graph.master.output.disconnect()
      for (const built of graph.channels.values()) built.output.disconnect()
    },
  }
}

/**
 * Renders the pattern offline, through the same graph, for bouncing onto the
 * timeline.
 *
 * A tail is rendered past the end of the pattern so a crash or an 808 that
 * outlasts the last step is not chopped off. The caller trims the clip to the
 * pattern length, which keeps it tiling seamlessly while leaving the tail
 * reachable by dragging the clip's edge.
 */
export async function renderRack(options: {
  rack: ChannelRack
  master: MasterSettings
  buffers: Map<string, AudioBuffer>
  bpm: number
  repeats: number
  sampleRate: number
}): Promise<{ buffer: AudioBuffer; patternSec: number }> {
  const { rack, master, buffers, bpm, repeats, sampleRate } = options
  const total = rackTotalSteps(rack)
  const stepSec = stepDuration(bpm, rack.stepsPerBar)
  const patternSec = total * stepSec * Math.max(1, repeats)

  let tail = 0
  for (const channel of rack.channels) {
    const buffer = buffers.get(channel.sourceUrl)
    if (!buffer) continue
    // The slowest hit is the longest one: a note pitched down an octave
    // plays at half speed and rings for twice as long.
    const slowest = channelHits(channel, total).reduce(
      (min, hit) => Math.min(min, hit.rate),
      playbackRate(channel),
    )
    tail = Math.max(tail, buffer.duration / Math.max(0.05, slowest))
  }
  // Capped, because a channel is not always a one-shot. Rack a whole song as
  // a sample and an untrimmed tail would render the entire song past the end
  // of a two-second pattern; eight seconds covers any 808 or crash worth
  // keeping and nothing worth waiting for.
  tail = Math.min(tail, MAX_TAIL_SEC)

  const length = Math.ceil((patternSec + tail) * sampleRate)
  const ctx = new OfflineAudioContext(2, Math.max(1, length), sampleRate)
  const graph = buildGraph(ctx, rack, master, ctx.destination)

  // The same channelHits() the live scheduler uses, so a bounce cannot
  // disagree with what was heard.
  for (let pass = 0; pass < Math.max(1, repeats); pass++) {
    const passStart = pass * total * stepSec
    for (const channel of rack.channels) {
      const built = graph.channels.get(channel.id)
      const buffer = buffers.get(channel.sourceUrl)
      if (!built || !buffer) continue
      for (const hit of channelHits(channel, total)) {
        const source = ctx.createBufferSource()
        source.buffer = buffer
        source.playbackRate.value = hit.rate
        let destination: AudioNode = built.input
        if (hit.gain < 0.999) {
          const shaper = ctx.createGain()
          shaper.gain.value = hit.gain
          shaper.connect(built.input)
          destination = shaper
        }
        source.connect(destination)
        const at = passStart + hit.step * stepSec
        source.start(at)
        if (hit.lengthSteps != null) source.stop(at + hit.lengthSteps * stepSec + 0.02)
      }
    }
  }

  return { buffer: await ctx.startRendering(), patternSec }
}
