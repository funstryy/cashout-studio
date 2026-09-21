import { apiFetch, apiJson } from './http'
import type { ModelId, OrchestratorStatus } from '../types'

export interface Yue2ModelSpecConfig {
  id: string
  family: string
  path: string
  task: string
  mode: string
}

export interface OrchestratorConfig {
  yue2_specs: {
    yue2: Yue2ModelSpecConfig
    sheetsage2: Yue2ModelSpecConfig
    muscriptor: Yue2ModelSpecConfig
    [key: string]: Yue2ModelSpecConfig
  }
}

export function getConfig(): Promise<OrchestratorConfig> {
  return apiFetch<OrchestratorConfig>('/api/orchestrator/config')
}

export function getStatus(): Promise<OrchestratorStatus> {
  return apiFetch<OrchestratorStatus>('/api/orchestrator/status')
}

export interface DownloadProgress {
  file: string
  percent: number
  downloaded: string
  total: string
  eta: string
  rate: string
}

/** Checkpoints an engine is fetching right now: empty once it's done. */
export async function getDownloads(model: ModelId = 'ace_step'): Promise<DownloadProgress[]> {
  const json = await apiFetch<{ downloads: DownloadProgress[] }>(
    `/api/orchestrator/downloads?model=${encodeURIComponent(model)}`,
  )
  return json.downloads
}

export function switchModel(model: ModelId): Promise<OrchestratorStatus> {
  return apiJson<OrchestratorStatus>('/api/orchestrator/switch', { model })
}

/** Omit the model to stop every running engine. */
export function stopModel(model?: ModelId): Promise<OrchestratorStatus> {
  return apiJson<OrchestratorStatus>(`/api/orchestrator/stop${model ? `?model=${model}` : ''}`, {})
}

export function stopActive(): Promise<OrchestratorStatus> {
  return apiJson<OrchestratorStatus>('/api/orchestrator/stop', {})
}
