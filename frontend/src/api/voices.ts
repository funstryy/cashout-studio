import { apiFetch, apiJson } from './http'

const BASE = '/api/voices'

export interface Voice {
  name: string
  transcript: string
  size_bytes?: number
  audio_url: string
}

/** 'vc' treats the source as speech, 'svc' as singing. */
export type ConversionTask = 'vc' | 'svc'

export interface ConversionJob {
  job_id?: string
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled' | 'unknown'
  error: string | null
  track_id: number | null
  voice?: string
}

export async function listVoices(): Promise<Voice[]> {
  const json = await apiFetch<{ voices: Voice[] }>(BASE)
  return json.voices
}

export interface ImportVoiceSource {
  file?: File
  trackId?: number
  /** Which stem of that track to use: 'vocals' gives the cleanest reference. */
  stem?: string
}

export function importVoice(name: string, source: ImportVoiceSource, transcript = ''): Promise<Voice> {
  const form = new FormData()
  form.append('name', name)
  form.append('transcript', transcript)
  if (source.file) form.append('audio', source.file)
  if (source.trackId != null) form.append('track_id', String(source.trackId))
  if (source.stem) form.append('stem', source.stem)
  return apiFetch<Voice>(BASE, { method: 'POST', body: form })
}

export async function deleteVoice(name: string): Promise<void> {
  await apiFetch(`${BASE}/${encodeURIComponent(name)}`, { method: 'DELETE' })
}

/** Returns the spoken audio itself, not JSON - the caller decides whether to
 * play it, save it as a track, or both. */
export async function speak(name: string, text: string, language = 'en'): Promise<Blob> {
  const resp = await fetch(`${BASE}/${encodeURIComponent(name)}/speak`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, language }),
  })
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      detail = (await resp.json())?.detail ?? detail
    } catch {
      // keep statusText
    }
    throw new Error(detail)
  }
  return resp.blob()
}

export interface ConvertOptions {
  task: ConversionTask
  title?: string
  trackId?: number
  stem?: string
  file?: File
}

export function convert(name: string, opts: ConvertOptions): Promise<ConversionJob> {
  const form = new FormData()
  form.append('task', opts.task)
  form.append('title', opts.title || '')
  if (opts.trackId != null) form.append('track_id', String(opts.trackId))
  if (opts.stem) form.append('stem', opts.stem)
  if (opts.file) form.append('audio', opts.file)
  return apiFetch<ConversionJob>(`${BASE}/${encodeURIComponent(name)}/convert`, { method: 'POST', body: form })
}

export function conversionStatus(jobId: string): Promise<ConversionJob> {
  return apiFetch<ConversionJob>(`${BASE}/jobs/${jobId}`)
}

export function cancelConversion(jobId: string): Promise<ConversionJob> {
  return apiJson<ConversionJob>(`${BASE}/jobs/${jobId}/cancel`, {})
}
