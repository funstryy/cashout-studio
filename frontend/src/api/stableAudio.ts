/**
 * Stable Audio 3, through the shared audio engine.
 *
 * The `/api/yue2` prefix is a misnomer inherited from when that server hosted
 * one model. It is audio.cpp's server, and it hosts a *family* per model
 * rather than a process per model - YuE2, Stable Audio, Chatterbox, Seed-VC
 * and the separation models are all inside it. Renaming the route would break
 * the voices and separation clients that already point at it for no gain, so
 * the name stays and this comment explains it.
 *
 * What this model is for here: the beat. Stable Audio 3 is instrumental -
 * it does not sing - but it leads every open model on tempo and key
 * adherence, runs in 8 diffusion steps where ACE-Step takes sixty, and is
 * the only edit-capable music model that runs on the GPU through Vulkan.
 * That last point is what makes generative fill usable on an AMD card at all.
 *
 * Vocals come from elsewhere: a take recorded on the timeline, re-voiced with
 * Seed-VC, or YuE2's vocal separated out of a full generation.
 */
import { apiFetch, apiJson } from './http'
import { ensureLoaded, runTask, getYue2Specs } from './yue2'
import type { TaskRunResult } from './yue2'

const BASE = '/api/yue2'

export type Sampler = 'pingpong' | 'euler' | 'dpmpp-2m' | 'dpmpp-3m-sde'

export const SAMPLERS: Sampler[] = ['pingpong', 'euler', 'dpmpp-2m', 'dpmpp-3m-sde']

export interface StableAudioOptions {
  prompt: string
  durationSeconds?: number
  steps?: number
  guidanceScale?: number
  seed?: number | null
  negativePrompt?: string
  sampler?: Sampler
  batchSize?: number
  /** Releases staged graph state between phases. Slower on repeat requests,
   *  but the difference between fitting a 6 GB card and not. */
  memSaver?: boolean
}

export interface InitAudioOptions extends StableAudioOptions {
  /** Server-side path from uploadAudio(). */
  audioPath: string
  /** 0 keeps the source almost intact; 1 ignores it. */
  initNoiseLevel?: number
}

export interface InpaintOptions extends StableAudioOptions {
  audioPath: string
  /** Several regions in one pass - the model masks them all together, so the
   *  fills are aware of each other rather than generated one at a time. */
  regions: { startSec: number; endSec: number }[]
}

function baseRequest(options: StableAudioOptions): Record<string, unknown> {
  const request: Record<string, unknown> = {
    text: options.prompt,
    duration_seconds: options.durationSeconds ?? 30,
    num_inference_steps: options.steps ?? 8,
    guidance_scale: options.guidanceScale ?? 1.0,
    options: {} as Record<string, string>,
  }
  if (options.seed != null) request.seed = options.seed

  const requestOptions = request.options as Record<string, string>
  if (options.negativePrompt?.trim()) requestOptions.negative_prompt = options.negativePrompt.trim()
  if (options.sampler) requestOptions.sampler = options.sampler
  if (options.batchSize && options.batchSize > 1) requestOptions.batch_size = String(options.batchSize)
  if (options.memSaver) requestOptions['stable_audio.mem_saver'] = 'true'
  return request
}

async function specId(): Promise<string> {
  const specs = await getYue2Specs()
  return specs.stable_audio?.id ?? 'stable_audio'
}

async function prepare(memSaver?: boolean): Promise<string> {
  const specs = await getYue2Specs()
  const spec = specs.stable_audio
  if (!spec) throw new Error('Stable Audio is not configured on this install')
  await ensureLoaded(spec, memSaver ? { 'stable_audio.mem_saver': 'true' } : undefined)
  return spec.id
}

/** Text to music. */
export async function generate(options: StableAudioOptions, signal?: AbortSignal): Promise<TaskRunResult> {
  const id = await prepare(options.memSaver)
  return runTask(id, baseRequest(options), signal)
}

/** Restyle existing audio, keeping its shape to the degree you choose. */
export async function initAudio(options: InitAudioOptions, signal?: AbortSignal): Promise<TaskRunResult> {
  const id = await prepare(options.memSaver)
  const request = baseRequest(options)
  request.audio = options.audioPath
  const requestOptions = request.options as Record<string, string>
  requestOptions.audio_input_kind = 'init_audio'
  requestOptions.init_noise_level = String(options.initNoiseLevel ?? 0.45)
  return runTask(id, request, signal)
}

/**
 * Regenerates one or more masked spans, leaving the rest alone.
 *
 * This is the engine behind generative fill. The mask is given as two
 * comma-separated lists of seconds, one of starts and one of ends, which is
 * how several regions get regenerated in a single pass with knowledge of each
 * other - not as separate jobs stitched together afterwards.
 */
export async function inpaint(options: InpaintOptions, signal?: AbortSignal): Promise<TaskRunResult> {
  if (!options.regions.length) throw new Error('no region selected')
  const id = await prepare(options.memSaver)
  const request = baseRequest(options)
  request.audio = options.audioPath
  const requestOptions = request.options as Record<string, string>
  requestOptions.audio_input_kind = 'inpaint_audio'
  requestOptions.inpaint_mask_start_seconds = options.regions.map((r) => r.startSec.toFixed(3)).join(',')
  requestOptions.inpaint_mask_end_seconds = options.regions.map((r) => r.endSec.toFixed(3)).join(',')
  return runTask(id, request, signal)
}

/** Whether the weights are actually on disk, so the tab can say so plainly
 *  rather than failing at the first generate. */
export async function isInstalled(): Promise<boolean> {
  try {
    const models = await apiFetch<{ data?: Array<{ id: string }> }>(`${BASE}/v1/models/available`)
    const ids = (models.data || []).map((m) => m.id)
    return ids.some((id) => id.includes('stable'))
  } catch {
    // The endpoint may not exist on older server builds; fall back to trying
    // a load, which is the only other way to find out.
    try {
      await prepare()
      return true
    } catch {
      return false
    }
  }
}

export { apiJson, specId }
