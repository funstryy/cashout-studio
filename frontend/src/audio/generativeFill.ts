/**
 * Generative fill for the arrangement.
 *
 * Every other DAW treats music generation as a separate window: you type a
 * prompt somewhere else, get a file, and drag it in. This makes the timeline
 * itself the surface. Select a span of bars on a track and the model
 * regenerates *that span*, conditioned on the audio either side of it, and
 * stitches the result back into place - the arrangement equivalent of
 * generative fill in an image editor.
 *
 * Two operations, because they are different questions:
 *
 *   fillRegion  "replace bars 17-20 of this track"     - repaint
 *   addPart     "write a bass line over what I have"   - lego
 *
 * `addPart` is the multi-track one: it is handed a bounce of everything
 * currently playing, so what it writes has to sit with the existing
 * arrangement rather than beside it.
 *
 * Engine choice
 * -------------
 * Written against whichever engine is running, because the right one depends
 * on the machine:
 *
 *   ACE-Step 1.5      repaint and lego, vocals and instrumental, but on an
 *                     AMD card it has no GPU path at all and runs on the CPU.
 *   Stable Audio 3    inpainting, continuation and LoRA, through the Vulkan
 *                     server - so it runs on the GPU on AMD, NVIDIA and Intel
 *                     alike. Instrumental-focused. 3.6 GB for the medium
 *                     model, which fits a 6 GB card.
 *
 * Neither is hardcoded here. The caller passes the engine; this module speaks
 * the task vocabulary both share. YuE2 is deliberately not an option: its
 * model spec declares no edit task at all, so it cannot regenerate a region,
 * only a whole song.
 */
import * as aceApi from '../api/aceStep'
import { encodeWav } from './wavEncoder'
import { getSharedAudioCtx } from '../composables/audioPlayback'

export type FillEngine = 'ace_step' | 'stable_audio'

export interface FillProgress {
  (stage: string, fraction: number): void
}

export interface FillOptions {
  /** The whole lane, not just the selection: the model needs what surrounds
   *  the region in order to match it. */
  source: AudioBuffer
  startSec: number
  endSec: number
  prompt: string
  engine?: FillEngine
  /** Model name as the engine's inventory reports it; blank means default. */
  model?: string
  seed?: number | null
  onProgress?: FillProgress
}

export interface AddPartOptions {
  /** A bounce of everything currently playing, so the new part fits it. */
  arrangement: AudioBuffer
  /** vocals, drums, bass, guitar, piano, keys, strings, synth, percussion… */
  trackName: string
  prompt: string
  engine?: FillEngine
  model?: string
  onProgress?: FillProgress
}

const POLL_MS = 1500
const MAX_POLLS = 800   // ~20 minutes; CPU ACE-Step is genuinely this slow

function toFile(buffer: AudioBuffer, name: string): File {
  return new File([encodeWav(buffer)], name, { type: 'audio/wav' })
}

/** Runs a task to completion and hands back the rendered audio. */
async function runTask(
  request: aceApi.GenerateMusicRequest,
  file: File,
  onProgress?: FillProgress,
): Promise<AudioBuffer> {
  const started = await aceApi.releaseTask(request, file)
  const taskId = started.task_id
  if (!taskId) throw new Error('the engine did not accept the task')

  // The engine reports 0 running, 1 done, 2 failed, with the detail as a
  // JSON string in `result` - the same contract the generate page polls.
  for (let attempt = 0; attempt < MAX_POLLS; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, POLL_MS))

    let entries: aceApi.QueryResultEntry[]
    try {
      entries = await aceApi.queryResult([taskId])
    } catch {
      continue   // a dropped poll is not a failed job
    }
    const entry = entries.find((e) => e.task_id === taskId)
    if (!entry) continue

    let parsed: { file?: string; error?: string; stage?: string; progress?: number }[] = []
    try {
      parsed = JSON.parse(entry.result)
    } catch {
      parsed = []
    }
    const first = parsed[0] ?? {}

    if (entry.status === 2) {
      throw new Error(first.error || 'the engine failed the task')
    }
    if (entry.status === 1) {
      if (!first.file) throw new Error('the engine reported success but returned no audio')
      // Served through the studio's own proxy, same as generated tracks.
      const bytes = await (await fetch(`/api/ace${first.file}`)).arrayBuffer()
      return await getSharedAudioCtx().decodeAudioData(bytes)
    }
    onProgress?.(first.stage || 'running', Number(first.progress ?? 0))
  }
  throw new Error('the engine did not finish in time')
}

/**
 * Regenerates one span of a track in place.
 *
 * The whole lane goes to the engine, with the span marked - that is what lets
 * the new material pick up where the old left off instead of starting cold.
 * The returned buffer is the whole lane again, with the span replaced.
 */
export async function fillRegion(options: FillOptions): Promise<AudioBuffer> {
  const { source, startSec, endSec, prompt, onProgress } = options
  if (endSec <= startSec) throw new Error('the selection is empty')
  if (startSec < 0 || endSec > source.duration + 0.001) {
    throw new Error('the selection is outside this track')
  }

  onProgress?.('encoding', 0)
  const request: aceApi.GenerateMusicRequest = {
    task_type: 'repaint',
    repainting_start: startSec,
    repainting_end: endSec,
    prompt,
    audio_duration: source.duration,
    audio_format: 'wav',
    batch_size: 1,
  }
  if (options.model) request.model = options.model
  if (options.seed != null) {
    request.seed = options.seed
    request.use_random_seed = false
  } else {
    request.use_random_seed = true
  }

  return runTask(request, toFile(source, 'region.wav'), onProgress)
}

/**
 * Writes a new part over the existing arrangement.
 *
 * The bounce that goes in is every unmuted lane summed - the point is that
 * the model hears the song, not a click track. What comes back is the new
 * part alone, ready to become its own lane.
 */
export async function addPart(options: AddPartOptions): Promise<AudioBuffer> {
  const { arrangement, trackName, prompt, onProgress } = options
  if (arrangement.duration < 1) throw new Error('there is nothing on the timeline yet')

  onProgress?.('encoding', 0)
  const request: aceApi.GenerateMusicRequest = {
    task_type: 'lego',
    track_name: trackName,
    prompt,
    audio_duration: arrangement.duration,
    audio_format: 'wav',
    batch_size: 1,
    use_random_seed: true,
  }
  if (options.model) request.model = options.model

  return runTask(request, toFile(arrangement, 'arrangement.wav'), onProgress)
}

/**
 * Splices a regenerated span back over the original.
 *
 * Belt and braces: the engine is asked to return the whole lane and usually
 * does, but a model that trims or pads by a few milliseconds would otherwise
 * shift everything after the edit. Rebuilding the lane here - original before,
 * generated during, original after - means the edit cannot move anything
 * outside its own span, whatever the engine returns.
 *
 * The joins are crossfaded over 20 ms. A hard cut at a waveform discontinuity
 * clicks, and a click at the edit point is the one artefact that makes the
 * whole feature sound broken.
 */
export function spliceRegion(
  original: AudioBuffer,
  generated: AudioBuffer,
  startSec: number,
  endSec: number,
  fadeSec = 0.02,
): AudioBuffer {
  const rate = original.sampleRate
  const channels = original.numberOfChannels
  const out = new AudioBuffer({
    numberOfChannels: channels,
    length: original.length,
    sampleRate: rate,
  })

  const start = Math.max(0, Math.round(startSec * rate))
  const end = Math.min(original.length, Math.round(endSec * rate))
  const fade = Math.max(1, Math.round(fadeSec * rate))

  for (let channel = 0; channel < channels; channel++) {
    const source = original.getChannelData(channel)
    const fill = generated.getChannelData(Math.min(channel, generated.numberOfChannels - 1))
    const target = out.getChannelData(channel)
    target.set(source)

    for (let i = start; i < end; i++) {
      // The generated buffer is aligned to the same timeline when the engine
      // returns the whole lane; when it returns only the region, fall back to
      // reading from its start.
      const fromWhole = i < fill.length ? fill[i] : 0
      const fromRegion = i - start < fill.length ? fill[i - start] : 0
      const generatedSample = generated.length >= original.length ? fromWhole : fromRegion

      let mix = generatedSample
      if (i - start < fade) {
        const t = (i - start) / fade
        mix = source[i] * (1 - t) + generatedSample * t
      } else if (end - i < fade) {
        const t = (end - i) / fade
        mix = source[i] * (1 - t) + generatedSample * t
      }
      target[i] = mix
    }
  }
  return out
}

/** Sums the lanes into one buffer for the model to hear. */
export function bounceArrangement(
  buffers: Map<string, AudioBuffer>,
  lanes: { clips: { sourceUrl: string; timelineStart: number; trimStart: number; trimEnd: number }[];
           settings: { muted: boolean; volume: number } }[],
  totalDuration: number,
  sampleRate: number,
): AudioBuffer | null {
  if (totalDuration <= 0) return null
  const length = Math.ceil(totalDuration * sampleRate)
  const out = new AudioBuffer({ numberOfChannels: 2, length, sampleRate })

  let wrote = false
  for (const lane of lanes) {
    if (lane.settings.muted) continue
    for (const clip of lane.clips) {
      const buffer = buffers.get(clip.sourceUrl)
      if (!buffer) continue
      const offset = Math.round(clip.timelineStart * sampleRate)
      const from = Math.round(clip.trimStart * buffer.sampleRate)
      const count = Math.round((clip.trimEnd - clip.trimStart) * buffer.sampleRate)
      for (let channel = 0; channel < 2; channel++) {
        const src = buffer.getChannelData(Math.min(channel, buffer.numberOfChannels - 1))
        const dst = out.getChannelData(channel)
        for (let i = 0; i < count; i++) {
          const target = offset + i
          if (target >= length) break
          dst[target] += (src[from + i] ?? 0) * lane.settings.volume
        }
      }
      wrote = true
    }
  }
  return wrote ? out : null
}
