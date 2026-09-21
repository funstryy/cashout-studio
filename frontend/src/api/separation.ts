import { apiFetch, apiJson } from './http'

const BASE = '/api/separation'

export interface SeparationModel {
  id: string
  label: string
  stems: string[]
  installed: boolean
}

export type EnsembleAlgorithm = 'max_spec' | 'min_spec' | 'average'
export type OutputFormat = 'wav' | 'flac' | 'mp3'
export type StemFilter = 'all' | 'vocals' | 'instrumental'

export interface SeparationJob {
  job_id?: string
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled' | 'unknown'
  error: string | null
  step: string
  progress: number
  stems: Record<string, string>
  track_id?: number | null
  models?: string[]
  algorithm?: EnsembleAlgorithm
}

export interface StartSeparationRequest {
  track_id: number
  models: string[]
  algorithm: EnsembleAlgorithm
  output_format: OutputFormat
  stem_filter: StemFilter
  sample_mode: boolean
  use_gpu: boolean
}

export async function listModels(): Promise<SeparationModel[]> {
  const json = await apiFetch<{ models: SeparationModel[] }>(`${BASE}/models`)
  return json.models
}

export function startSeparation(req: StartSeparationRequest): Promise<SeparationJob> {
  return apiJson<SeparationJob>(`${BASE}/start`, req)
}

export function separationStatus(jobId: string): Promise<SeparationJob> {
  return apiFetch<SeparationJob>(`${BASE}/jobs/${jobId}`)
}

export function cancelSeparation(jobId: string): Promise<SeparationJob> {
  return apiJson<SeparationJob>(`${BASE}/jobs/${jobId}/cancel`, {})
}
