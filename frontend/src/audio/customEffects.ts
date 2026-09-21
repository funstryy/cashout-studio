/**
 * Home-made plugins.
 *
 * A VST in this app is printed: native code cannot run inside the browser
 * that plays the timeline, so applying one renders the track and swaps the
 * audio. These are the opposite - they are built out of Web Audio nodes, so
 * they sit in the live signal path and you hear a knob move while the song
 * is playing. That is the whole reason for them to exist alongside the VST
 * host rather than instead of it.
 *
 * A plugin here is a chain: an ordered list of primitives, each with its own
 * parameters. Saving one saves the recipe, not any audio, so a plugin is a
 * few hundred bytes and can be applied to anything.
 *
 * The primitives are chosen to be the smallest set that covers what people
 * actually reach for, and every one of them maps onto nodes the engine
 * already has. Nothing here needs an AudioWorklet, which matters: a worklet
 * would have to be shipped, compiled and kept alive per context, and the
 * studio already carries enough moving parts.
 */

export type EffectKind =
  | 'gain'
  | 'filter'
  | 'drive'
  | 'bitcrush'
  | 'delay'
  | 'reverb'
  | 'compressor'
  | 'chorus'
  | 'tremolo'
  | 'pan'

export interface EffectParam {
  key: string
  label: string
  min: number
  max: number
  step: number
  default: number
  unit?: string
}

export interface EffectDef {
  kind: EffectKind
  label: string
  /** One line, shown under the node in the designer. */
  blurb: string
  params: EffectParam[]
}

export interface EffectNode {
  id: string
  kind: EffectKind
  bypassed?: boolean
  values: Record<string, number>
}

export interface CustomPlugin {
  id: string
  name: string
  chain: EffectNode[]
}

export const EFFECTS: Record<EffectKind, EffectDef> = {
  gain: {
    kind: 'gain', label: 'Gain', blurb: 'Level, before everything after it.',
    params: [{ key: 'gain', label: 'Gain', min: 0, max: 3, step: 0.01, default: 1, unit: '×' }],
  },
  filter: {
    kind: 'filter', label: 'Filter', blurb: 'Low-pass, high-pass or a bell.',
    params: [
      // 0 lowpass, 1 highpass, 2 bandpass, 3 peaking - an index rather than a
      // string so every parameter in the app is one number and the save
      // format stays trivial.
      { key: 'type', label: 'Type', min: 0, max: 3, step: 1, default: 0 },
      { key: 'freq', label: 'Frequency', min: 30, max: 18000, step: 10, default: 1200, unit: 'Hz' },
      { key: 'q', label: 'Resonance', min: 0.1, max: 18, step: 0.1, default: 1 },
      { key: 'gain', label: 'Bell gain', min: -24, max: 24, step: 0.5, default: 0, unit: 'dB' },
    ],
  },
  drive: {
    kind: 'drive', label: 'Drive', blurb: 'Saturation, from warm to broken.',
    params: [
      { key: 'amount', label: 'Drive', min: 0, max: 100, step: 1, default: 25 },
      { key: 'mix', label: 'Mix', min: 0, max: 1, step: 0.01, default: 1 },
    ],
  },
  bitcrush: {
    kind: 'bitcrush', label: 'Bitcrush', blurb: 'Quantise the waveform. Lo-fi, harsh.',
    params: [
      { key: 'bits', label: 'Bits', min: 1, max: 16, step: 1, default: 8 },
      { key: 'mix', label: 'Mix', min: 0, max: 1, step: 0.01, default: 1 },
    ],
  },
  delay: {
    kind: 'delay', label: 'Delay', blurb: 'Echo with feedback.',
    params: [
      { key: 'time', label: 'Time', min: 0.01, max: 2, step: 0.01, default: 0.3, unit: 's' },
      { key: 'feedback', label: 'Feedback', min: 0, max: 0.95, step: 0.01, default: 0.35 },
      { key: 'mix', label: 'Mix', min: 0, max: 1, step: 0.01, default: 0.3 },
    ],
  },
  reverb: {
    kind: 'reverb', label: 'Reverb', blurb: 'Space, from a room to a cavern.',
    params: [
      { key: 'size', label: 'Size', min: 0.2, max: 6, step: 0.1, default: 2, unit: 's' },
      { key: 'mix', label: 'Mix', min: 0, max: 1, step: 0.01, default: 0.25 },
    ],
  },
  compressor: {
    kind: 'compressor', label: 'Compressor', blurb: 'Tame the peaks, lift the rest.',
    params: [
      { key: 'threshold', label: 'Threshold', min: -60, max: 0, step: 1, default: -24, unit: 'dB' },
      { key: 'ratio', label: 'Ratio', min: 1, max: 20, step: 0.5, default: 4 },
      { key: 'attack', label: 'Attack', min: 0, max: 0.2, step: 0.001, default: 0.005, unit: 's' },
      { key: 'release', label: 'Release', min: 0.01, max: 1, step: 0.01, default: 0.2, unit: 's' },
    ],
  },
  chorus: {
    kind: 'chorus', label: 'Chorus', blurb: 'Detuned doubling. Widens a thin part.',
    params: [
      { key: 'rate', label: 'Rate', min: 0.05, max: 8, step: 0.05, default: 0.8, unit: 'Hz' },
      { key: 'depth', label: 'Depth', min: 0, max: 0.02, step: 0.0005, default: 0.004, unit: 's' },
      { key: 'mix', label: 'Mix', min: 0, max: 1, step: 0.01, default: 0.4 },
    ],
  },
  tremolo: {
    kind: 'tremolo', label: 'Tremolo', blurb: 'Level wobble on a timer.',
    params: [
      { key: 'rate', label: 'Rate', min: 0.1, max: 20, step: 0.1, default: 5, unit: 'Hz' },
      { key: 'depth', label: 'Depth', min: 0, max: 1, step: 0.01, default: 0.5 },
    ],
  },
  pan: {
    kind: 'pan', label: 'Pan', blurb: 'Position in the stereo field.',
    params: [{ key: 'pan', label: 'Pan', min: -1, max: 1, step: 0.01, default: 0 }],
  },
}

export const EFFECT_ORDER: EffectKind[] = [
  'gain', 'filter', 'drive', 'bitcrush', 'compressor', 'chorus', 'tremolo', 'delay', 'reverb', 'pan',
]

export function newEffect(kind: EffectKind): EffectNode {
  const values: Record<string, number> = {}
  for (const param of EFFECTS[kind].params) values[param.key] = param.default
  return { id: crypto.randomUUID(), kind, values }
}

// ----------------------------------------------------------------- building

const FILTER_TYPES: BiquadFilterType[] = ['lowpass', 'highpass', 'bandpass', 'peaking']

/** A saturation curve. tanh-shaped, so it rounds into clipping instead of
 *  hitting a wall - which is the difference between "driven" and "broken". */
function driveCurve(amount: number): Float32Array<ArrayBuffer> {
  const samples = 1024
  const curve = new Float32Array(new ArrayBuffer(samples * 4))
  const k = Math.max(0.0001, amount) * 0.6
  for (let i = 0; i < samples; i++) {
    const x = (i * 2) / samples - 1
    curve[i] = Math.tanh(k * x) / Math.tanh(k || 1)
  }
  return curve
}

/** Step the signal onto 2^bits levels. A waveshaper is the cheap way to do
 *  this without a worklet: the curve *is* the quantiser. */
function crushCurve(bits: number): Float32Array<ArrayBuffer> {
  const samples = 2048
  const curve = new Float32Array(new ArrayBuffer(samples * 4))
  const levels = Math.max(2, Math.pow(2, Math.round(bits)))
  for (let i = 0; i < samples; i++) {
    const x = (i * 2) / samples - 1
    curve[i] = Math.round(x * levels) / levels
  }
  return curve
}

const impulseCache = new Map<string, AudioBuffer>()

function impulse(ctx: BaseAudioContext, seconds: number): AudioBuffer {
  const key = `${ctx.sampleRate}:${seconds.toFixed(2)}`
  const cached = impulseCache.get(key)
  if (cached) return cached
  const length = Math.max(1, Math.floor(ctx.sampleRate * seconds))
  const buffer = new AudioBuffer({ numberOfChannels: 2, length, sampleRate: ctx.sampleRate })
  for (let channel = 0; channel < 2; channel++) {
    const data = buffer.getChannelData(channel)
    for (let i = 0; i < length; i++) {
      data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / length, 2.2)
    }
  }
  impulseCache.set(key, buffer)
  return buffer
}

export interface BuiltChain {
  input: AudioNode
  output: AudioNode
  /** Oscillators driving modulation, so they can be stopped on teardown -
   *  a chorus left running keeps its context awake forever. */
  running: AudioScheduledSourceNode[]
  dispose: () => void
}

/**
 * Turns a chain into live audio nodes.
 *
 * Wet/dry is done with a parallel pair rather than a single mix knob on the
 * effect, because most of these have no native mix and a parallel path is
 * the only way to get one that behaves at both extremes.
 */
export function buildChain(ctx: BaseAudioContext, chain: EffectNode[]): BuiltChain {
  const input = ctx.createGain()
  const running: AudioScheduledSourceNode[] = []
  const created: AudioNode[] = [input]
  let cursor: AudioNode = input

  const wet = (node: AudioNode, tail: AudioNode, mix: number): AudioNode => {
    if (mix >= 0.999) {
      cursor.connect(node)
      return tail
    }
    const dryGain = ctx.createGain()
    const wetGain = ctx.createGain()
    const sum = ctx.createGain()
    dryGain.gain.value = 1 - mix
    wetGain.gain.value = mix
    cursor.connect(dryGain).connect(sum)
    cursor.connect(node)
    tail.connect(wetGain).connect(sum)
    created.push(dryGain, wetGain, sum)
    return sum
  }

  for (const step of chain) {
    if (step.bypassed) continue
    const v = step.values

    switch (step.kind) {
      case 'gain': {
        const node = ctx.createGain()
        node.gain.value = v.gain
        cursor.connect(node)
        cursor = node
        created.push(node)
        break
      }
      case 'pan': {
        const node = ctx.createStereoPanner()
        node.pan.value = v.pan
        cursor.connect(node)
        cursor = node
        created.push(node)
        break
      }
      case 'filter': {
        const node = ctx.createBiquadFilter()
        node.type = FILTER_TYPES[Math.round(v.type)] ?? 'lowpass'
        node.frequency.value = v.freq
        node.Q.value = v.q
        node.gain.value = v.gain
        cursor.connect(node)
        cursor = node
        created.push(node)
        break
      }
      case 'drive': {
        const shaper = ctx.createWaveShaper()
        shaper.curve = driveCurve(v.amount)
        shaper.oversample = '4x'
        cursor = wet(shaper, shaper, v.mix)
        created.push(shaper)
        break
      }
      case 'bitcrush': {
        const shaper = ctx.createWaveShaper()
        shaper.curve = crushCurve(v.bits)
        cursor = wet(shaper, shaper, v.mix)
        created.push(shaper)
        break
      }
      case 'compressor': {
        const node = ctx.createDynamicsCompressor()
        node.threshold.value = v.threshold
        node.ratio.value = v.ratio
        node.attack.value = v.attack
        node.release.value = v.release
        cursor.connect(node)
        cursor = node
        created.push(node)
        break
      }
      case 'delay': {
        const delay = ctx.createDelay(2.5)
        delay.delayTime.value = v.time
        const feedback = ctx.createGain()
        feedback.gain.value = v.feedback
        delay.connect(feedback).connect(delay)
        cursor = wet(delay, delay, v.mix)
        created.push(delay, feedback)
        break
      }
      case 'reverb': {
        const convolver = ctx.createConvolver()
        convolver.buffer = impulse(ctx, v.size)
        cursor = wet(convolver, convolver, v.mix)
        created.push(convolver)
        break
      }
      case 'chorus': {
        const delay = ctx.createDelay(0.1)
        delay.delayTime.value = 0.02
        const lfo = ctx.createOscillator()
        const depth = ctx.createGain()
        lfo.frequency.value = v.rate
        depth.gain.value = v.depth
        lfo.connect(depth).connect(delay.delayTime)
        lfo.start()
        running.push(lfo)
        cursor = wet(delay, delay, v.mix)
        created.push(delay, depth)
        break
      }
      case 'tremolo': {
        const node = ctx.createGain()
        // Centred so depth 1 swings between silence and unity rather than
        // doubling the level on the peaks.
        node.gain.value = 1 - v.depth / 2
        const lfo = ctx.createOscillator()
        const depth = ctx.createGain()
        lfo.frequency.value = v.rate
        depth.gain.value = v.depth / 2
        lfo.connect(depth).connect(node.gain)
        lfo.start()
        running.push(lfo)
        cursor.connect(node)
        cursor = node
        created.push(node, depth)
        break
      }
    }
  }

  const output = ctx.createGain()
  cursor.connect(output)
  created.push(output)

  return {
    input,
    output,
    running,
    dispose() {
      for (const source of running) {
        try {
          source.stop()
        } catch {
          // Already stopped.
        }
      }
      for (const node of created) node.disconnect()
    },
  }
}

// ----------------------------------------------------------------- from words

interface Rule {
  match: RegExp
  build: () => EffectNode[]
}

function withValues(kind: EffectKind, values: Record<string, number>): EffectNode {
  const node = newEffect(kind)
  Object.assign(node.values, values)
  return node
}

/**
 * A starting chain from a description.
 *
 * Rules, not a model. The vocabulary producers use for this is small and
 * stable - "warm", "lo-fi", "wide", "telephone" mean the same handful of
 * things to everyone - and a lookup table gets them right instantly, offline,
 * and identically every time. It is a starting point to be dragged around,
 * not an oracle, and the designer says so.
 */
const RULES: Rule[] = [
  {
    match: /\b(lo-?fi|vintage|tape|old|dusty|vinyl)\b/i,
    build: () => [
      withValues('filter', { type: 0, freq: 6500, q: 0.7 }),
      withValues('bitcrush', { bits: 10, mix: 0.5 }),
      withValues('drive', { amount: 18, mix: 0.6 }),
    ],
  },
  {
    match: /\b(warm|analog|analogue|saturat\w*|thick|fat)\b/i,
    build: () => [
      withValues('drive', { amount: 22, mix: 0.7 }),
      withValues('filter', { type: 3, freq: 180, q: 0.8, gain: 3 }),
    ],
  },
  {
    match: /\b(telephone|radio|phone|lofi vocal|megaphone)\b/i,
    build: () => [
      withValues('filter', { type: 1, freq: 700, q: 1 }),
      withValues('filter', { type: 0, freq: 3200, q: 1 }),
      withValues('drive', { amount: 35, mix: 0.8 }),
    ],
  },
  {
    match: /\b(wide|widen|stereo|lush|shimmer|dreamy)\b/i,
    build: () => [
      withValues('chorus', { rate: 0.5, depth: 0.006, mix: 0.45 }),
      withValues('reverb', { size: 3, mix: 0.3 }),
    ],
  },
  {
    match: /\b(space|hall|cavern|ambient|huge|cathedral)\b/i,
    build: () => [withValues('reverb', { size: 5, mix: 0.45 })],
  },
  {
    match: /\b(slap|echo|delay|dub)\b/i,
    build: () => [withValues('delay', { time: 0.24, feedback: 0.42, mix: 0.35 })],
  },
  {
    match: /\b(punch\w*|tight|glue|compress\w*|loud)\b/i,
    build: () => [withValues('compressor', { threshold: -20, ratio: 6, attack: 0.004, release: 0.15 })],
  },
  {
    match: /\b(distort\w*|crunch\w*|aggressive|hard|broken|destroy)\b/i,
    build: () => [
      withValues('drive', { amount: 65, mix: 1 }),
      withValues('filter', { type: 0, freq: 9000, q: 0.7 }),
    ],
  },
  {
    match: /\b(wobble|trem\w*|pulse|throb)\b/i,
    build: () => [withValues('tremolo', { rate: 5.5, depth: 0.6 })],
  },
  {
    match: /\b(dark|muffled|underwater|behind)\b/i,
    build: () => [withValues('filter', { type: 0, freq: 900, q: 0.8 })],
  },
  {
    match: /\b(bright|air|crisp|sparkle|presence)\b/i,
    build: () => [withValues('filter', { type: 3, freq: 9000, q: 0.7, gain: 5 })],
  },
]

export interface SeedResult {
  chain: EffectNode[]
  /** Which words were recognised, so the designer can say what it heard
   *  rather than silently producing something unexplained. */
  matched: string[]
}

export function chainFromDescription(description: string): SeedResult {
  const chain: EffectNode[] = []
  const matched: string[] = []

  for (const rule of RULES) {
    const hit = description.match(rule.match)
    if (!hit) continue
    matched.push(hit[0].toLowerCase())
    chain.push(...rule.build())
  }

  if (!chain.length) {
    // Nothing recognised: a neutral chain is a better starting point than an
    // empty one, and far better than a guess dressed up as understanding.
    chain.push(newEffect('filter'), newEffect('compressor'))
  }
  return { chain: dedupe(chain), matched }
}

/**
 * Collapses what overlapping rules both asked for.
 *
 * Several descriptions legitimately imply the same effect - "warm" and
 * "lo-fi" both want saturation - and naively concatenating their chains
 * stacks two drives in series, which is not "warm and lo-fi", it is twice as
 * distorted. Measured on "warm lo-fi tape, wide and dark": eight nodes, two
 * drives and three filters fighting each other.
 *
 * Filters are keyed by type as well as kind, because a high-pass and a
 * low-pass together are a band - the telephone rule depends on exactly that
 * pair surviving.
 *
 * First occurrence wins: the rules are ordered most-specific first, so the
 * earlier match is the more deliberate one.
 */
function dedupe(chain: EffectNode[]): EffectNode[] {
  const seen = new Set<string>()
  const out: EffectNode[] = []
  for (const node of chain) {
    const key = node.kind === 'filter' ? `filter:${Math.round(node.values.type)}` : node.kind
    if (seen.has(key)) continue
    seen.add(key)
    out.push(node)
  }
  return out
}
