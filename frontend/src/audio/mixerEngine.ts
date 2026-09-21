/**
 * Web Audio mixing engine for recombining separated stems with per-channel
 * volume/mute/solo/pan/EQ/compressor/reverb, plus a master bus with the same
 * effects (minus pan/mute/solo). The graph-builder is parameterized over
 * BaseAudioContext so the exact same code builds the live preview graph (a
 * real AudioContext) and the export render graph (an OfflineAudioContext) -
 * this is what guarantees the exported mix sounds identical to the preview.
 */
import { getSharedAudioCtx } from '../composables/audioPlayback'

export interface EqSettings {
  low: number
  mid: number
  high: number
}

export interface CompSettings {
  threshold: number
  ratio: number
}

export interface ReverbSettings {
  mix: number
}

export interface ChannelSettings {
  volume: number
  muted: boolean
  solo: boolean
  pan: number
  eq: EqSettings
  comp: CompSettings
  reverb: ReverbSettings
}

export type MasterSettings = Omit<ChannelSettings, 'pan' | 'muted' | 'solo'>

export type StemName = 'vocals' | 'drums' | 'bass' | 'other'
export const STEM_NAMES: StemName[] = ['vocals', 'drums', 'bass', 'other']

export interface MixSettings {
  version: 1
  stems: Record<StemName, ChannelSettings>
  master: MasterSettings
}

export function defaultChannelSettings(): ChannelSettings {
  return {
    volume: 1,
    muted: false,
    solo: false,
    pan: 0,
    eq: { low: 0, mid: 0, high: 0 },
    comp: { threshold: -24, ratio: 1 },
    reverb: { mix: 0 },
  }
}

/**
 * Fills in whatever a channel's settings are missing.
 *
 * Anything arriving from outside this machine - a collaborator's lane, a
 * project saved before a control existed - can be short of a field, and the
 * mixer reads them without asking: `s.eq.low` on a lane with no eq throws
 * during render and takes the page with it. Measured on a lane that came
 * over a live session with only volume and pan set.
 */
export function withChannelDefaults(partial: Partial<ChannelSettings> | undefined): ChannelSettings {
  const base = defaultChannelSettings()
  if (!partial) return base
  return {
    ...base,
    ...partial,
    eq: { ...base.eq, ...(partial.eq ?? {}) },
    comp: { ...base.comp, ...(partial.comp ?? {}) },
    reverb: { ...base.reverb, ...(partial.reverb ?? {}) },
  }
}

export function defaultMasterSettings(): MasterSettings {
  const { pan: _pan, muted: _muted, solo: _solo, ...rest } = defaultChannelSettings()
  return rest
}

export function defaultMixSettings(): MixSettings {
  return {
    version: 1,
    stems: {
      vocals: defaultChannelSettings(),
      drums: defaultChannelSettings(),
      bass: defaultChannelSettings(),
      other: defaultChannelSettings(),
    },
    master: defaultMasterSettings(),
  }
}

export interface BuiltChannel {
  input: AudioNode
  output: AudioNode
  volumeGain: GainNode
  eqLow: BiquadFilterNode
  eqMid: BiquadFilterNode
  eqHigh: BiquadFilterNode
  comp: DynamicsCompressorNode
  panner: StereoPannerNode | null
  dryGain: GainNode
  wetGain: GainNode
  convolver: ConvolverNode
  analyser: AnalyserNode | null
}

export function buildChannel(ctx: BaseAudioContext, isMaster: boolean, impulse: AudioBuffer): BuiltChannel {
  const volumeGain = ctx.createGain()
  const eqLow = ctx.createBiquadFilter()
  eqLow.type = 'lowshelf'
  eqLow.frequency.value = 320
  const eqMid = ctx.createBiquadFilter()
  eqMid.type = 'peaking'
  eqMid.frequency.value = 1000
  eqMid.Q.value = 0.7
  const eqHigh = ctx.createBiquadFilter()
  eqHigh.type = 'highshelf'
  eqHigh.frequency.value = 3200
  const comp = ctx.createDynamicsCompressor()
  const panner = isMaster ? null : ctx.createStereoPanner()
  const dryGain = ctx.createGain()
  const wetGain = ctx.createGain()
  const convolver = ctx.createConvolver()
  convolver.buffer = impulse
  const reverbOut = ctx.createGain()

  let input: AudioNode
  let preReverb: AudioNode
  if (isMaster) {
    // Stems fan their outputs directly into eqLow - Web Audio sums multiple
    // incoming connections on one node natively, no separate "sum" node needed.
    input = eqLow
    eqLow.connect(eqMid)
    eqMid.connect(eqHigh)
    eqHigh.connect(comp)
    preReverb = comp
  } else {
    input = volumeGain
    volumeGain.connect(eqLow)
    eqLow.connect(eqMid)
    eqMid.connect(eqHigh)
    eqHigh.connect(comp)
    comp.connect(panner!)
    preReverb = panner!
  }

  preReverb.connect(dryGain)
  preReverb.connect(convolver)
  convolver.connect(wetGain)
  dryGain.connect(reverbOut)
  wetGain.connect(reverbOut)

  // Stems: volume is first in the chain (already the `input`). Master:
  // volume is deliberately LAST, after the reverb mix - do not swap these.
  const output = isMaster ? volumeGain : reverbOut
  if (isMaster) reverbOut.connect(volumeGain)

  let analyser: AnalyserNode | null = null
  if (typeof ctx.createAnalyser === 'function') {
    try {
      analyser = ctx.createAnalyser()
      analyser.fftSize = 256
      analyser.smoothingTimeConstant = 0.3
      output.connect(analyser)
    } catch {
      // OfflineAudioContext or unsupported
    }
  }

  return { input, output, volumeGain, eqLow, eqMid, eqHigh, comp, panner, dryGain, wetGain, convolver, analyser }
}

export function applyChannelSettings(ch: BuiltChannel, s: ChannelSettings | MasterSettings, effectiveVolume: number): void {
  ch.volumeGain.gain.value = effectiveVolume
  ch.eqLow.gain.value = s.eq.low
  ch.eqMid.gain.value = s.eq.mid
  ch.eqHigh.gain.value = s.eq.high
  ch.comp.threshold.value = s.comp.threshold
  ch.comp.ratio.value = s.comp.ratio
  if (ch.panner && 'pan' in s) ch.panner.pan.value = (s as ChannelSettings).pan
  const mix = Math.max(0, Math.min(1, s.reverb.mix))
  ch.dryGain.gain.value = 1 - mix
  ch.wetGain.gain.value = mix
}

function effectiveStemGain(s: ChannelSettings, anySolo: boolean): number {
  if (s.muted) return 0
  if (anySolo && !s.solo) return 0
  return s.volume
}

export interface MixGraph {
  ctx: BaseAudioContext
  stems: Record<StemName, BuiltChannel>
  master: BuiltChannel
}

export function buildMixGraph(ctx: BaseAudioContext, impulse: AudioBuffer): MixGraph {
  const master = buildChannel(ctx, true, impulse)
  master.output.connect(ctx.destination)
  const stems = {} as Record<StemName, BuiltChannel>
  for (const name of STEM_NAMES) {
    const ch = buildChannel(ctx, false, impulse)
    ch.output.connect(master.input)
    stems[name] = ch
  }
  return { ctx, stems, master }
}

export function applyMixSettings(graph: MixGraph, settings: MixSettings): void {
  const anySolo = STEM_NAMES.some((n) => settings.stems[n].solo)
  for (const name of STEM_NAMES) {
    applyChannelSettings(graph.stems[name], settings.stems[name], effectiveStemGain(settings.stems[name], anySolo))
  }
  applyChannelSettings(graph.master, settings.master, settings.master.volume)
}

function disconnectChannel(ch: BuiltChannel): void {
  ch.volumeGain.disconnect()
  ch.eqLow.disconnect()
  ch.eqMid.disconnect()
  ch.eqHigh.disconnect()
  ch.comp.disconnect()
  ch.panner?.disconnect()
  ch.dryGain.disconnect()
  ch.wetGain.disconnect()
  ch.convolver.disconnect()
  ch.analyser?.disconnect()
}

export function getChannelLevel(ch: BuiltChannel): { peak: number; clipping: boolean } {
  if (!ch.analyser) return { peak: 0, clipping: false }
  const data = new Float32Array(ch.analyser.fftSize)
  ch.analyser.getFloatTimeDomainData(data)
  let max = 0
  for (let i = 0; i < data.length; i++) {
    const val = Math.abs(data[i])
    if (val > max) max = val
  }
  return {
    peak: max,
    clipping: max >= 0.99,
  }
}

/** Must be called when the mixer closes, or Chrome/Firefox keep the (silent
 * but allocated) processing graph alive across repeated open/close cycles. */
export function disconnectMixGraph(graph: MixGraph): void {
  for (const name of STEM_NAMES) disconnectChannel(graph.stems[name])
  disconnectChannel(graph.master)
}

const impulseCache = new Map<number, AudioBuffer>()

/** A procedurally generated ~2s decaying-noise impulse response, cached per
 * sample rate. Unlike AudioBufferSourceNode, ConvolverNode.buffer does NOT
 * auto-resample - its sample rate must exactly match the context's, so the
 * impulse has to be (re)generated for whatever rate the live AudioContext
 * (device-dependent, e.g. 48000Hz) or the export OfflineAudioContext uses. */
export function getReverbImpulse(sampleRate: number): AudioBuffer {
  let buf = impulseCache.get(sampleRate)
  if (!buf) {
    buf = generateImpulse(sampleRate, 2, 2)
    impulseCache.set(sampleRate, buf)
  }
  return buf
}

function generateImpulse(sampleRate: number, durationSec: number, decay: number): AudioBuffer {
  const length = Math.floor(sampleRate * durationSec)
  const buffer = new AudioBuffer({ numberOfChannels: 2, length, sampleRate })
  for (let channel = 0; channel < 2; channel++) {
    const data = buffer.getChannelData(channel)
    for (let i = 0; i < length; i++) {
      data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / length, decay)
    }
  }
  return buffer
}

// Keyed by stem URL so reopening the mixer for the same track within the
// session skips re-fetching/re-decoding the WAV files (mirrors peaksCache
// in composables/audioPlayback.ts).
const bufferCache = new Map<string, Promise<AudioBuffer>>()

export function decodeStem(url: string): Promise<AudioBuffer> {
  let entry = bufferCache.get(url)
  if (!entry) {
    entry = fetch(url)
      .then((r) => r.arrayBuffer())
      .then((buf) => getSharedAudioCtx().decodeAudioData(buf))
    bufferCache.set(url, entry)
  }
  return entry
}

export interface PlaybackHandle {
  stop(): void
}

/** Starts all 4 stem sources at the same AudioContext time for sample-accurate
 * sync. AudioBufferSourceNodes are one-shot, so pause/seek is implemented by
 * the caller as stop() + a fresh playFrom() at the new offset - the effect
 * graph itself (built once by buildMixGraph) is never rebuilt here. */
export function playFrom(
  graph: MixGraph,
  buffers: Record<StemName, AudioBuffer>,
  offsetSec: number,
  onEnded: () => void,
): PlaybackHandle {
  const ctx = graph.ctx as AudioContext
  let longest: StemName = STEM_NAMES[0]
  for (const name of STEM_NAMES) {
    if (buffers[name].duration > buffers[longest].duration) longest = name
  }
  const sources: AudioBufferSourceNode[] = STEM_NAMES.map((name) => {
    const src = ctx.createBufferSource()
    src.buffer = buffers[name]
    src.connect(graph.stems[name].input)
    src.start(0, Math.min(offsetSec, Math.max(0, buffers[name].duration - 0.001)))
    if (name === longest) src.onended = () => onEnded()
    return src
  })
  return {
    stop() {
      for (const src of sources) {
        src.onended = null
        try {
          src.stop()
        } catch {
          // already stopped/ended - fine
        }
        src.disconnect()
      }
    },
  }
}

/** Renders the mix offline (identical graph/settings code path as the live
 * preview) and returns the final stereo AudioBuffer, ready to encode. */
export async function renderMix(
  buffers: Record<StemName, AudioBuffer>,
  settings: MixSettings,
  sampleRate: number,
): Promise<AudioBuffer> {
  const durationSec = Math.max(...STEM_NAMES.map((n) => buffers[n].duration))
  const ctx = new OfflineAudioContext(2, Math.ceil(durationSec * sampleRate), sampleRate)
  const graph = buildMixGraph(ctx, getReverbImpulse(sampleRate))
  applyMixSettings(graph, settings)
  for (const name of STEM_NAMES) {
    const src = ctx.createBufferSource()
    src.buffer = buffers[name]
    src.connect(graph.stems[name].input)
    src.start(0)
  }
  return ctx.startRendering()
}
