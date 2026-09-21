import { apiFetch, apiJson } from './http'

const BASE = '/api/plugins'

export interface InstalledPlugin {
  name: string
  format: 'vst3' | 'vst2'
  path: string
  bundle_path: string
  size_mb: number
  arch: string
  class_names: string[]
}

export interface PluginInventory {
  vst3: InstalledPlugin[]
  vst2: InstalledPlugin[]
  host_available: boolean
  searched: { vst3: string[]; vst2: string[] }
}

export interface PluginParameter {
  index: number
  id: number
  title: string
  units: string
  default: number
  steps: number
}

export interface PluginDescription {
  ok: boolean
  error?: string
  name?: string
  vendor?: string
  category?: string
  audio_inputs?: number
  audio_outputs?: number
  parameters?: PluginParameter[]
}

export interface PluginJob {
  job_id?: string
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled' | 'unknown'
  error: string | null
  track_id: number | null
  plugin?: string
}

export function listPlugins(refresh = false): Promise<PluginInventory> {
  return apiFetch<PluginInventory>(`${BASE}?refresh=${refresh}`)
}

/** Loads the plugin in the host process to read its real parameter list. */
export function describePlugin(path: string): Promise<PluginDescription> {
  return apiFetch<PluginDescription>(`${BASE}/describe?path=${encodeURIComponent(path)}`)
}

export interface PluginEditorJob {
  job_id?: string
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled' | 'unknown'
  error: string | null
  state_key: string | null
  plugin?: string
}

/**
 * Opens the plugin's own interface in a native window.
 *
 * The window is drawn by the plugin in the host process - there is no way to
 * put it inside this page, and no reason to want one: it is the interface the
 * plugin was designed around. Resolves as soon as the window is on screen;
 * the job it returns finishes when the user closes it.
 */
export function openPluginEditor(body: {
  plugin_path: string
  state_key: string
}): Promise<PluginEditorJob> {
  return apiJson<PluginEditorJob>(`${BASE}/editor`, body)
}

export function pluginEditorStatus(jobId: string): Promise<PluginEditorJob> {
  return apiFetch<PluginEditorJob>(`${BASE}/editor/${jobId}`)
}

export function closePluginEditor(jobId: string): Promise<PluginEditorJob> {
  return apiJson<PluginEditorJob>(`${BASE}/editor/${jobId}/close`, {})
}

/** Whether this slot already holds settings made in the plugin's window. */
export function pluginStateSaved(stateKey: string): Promise<{ state_key: string; saved: boolean }> {
  return apiFetch(`${BASE}/state?state_key=${encodeURIComponent(stateKey)}`)
}

export function processWithPlugin(body: {
  plugin_path: string
  track_id: number
  stem?: string
  title?: string
  state_key?: string
  params?: Record<string, number>
}): Promise<PluginJob> {
  return apiJson<PluginJob>(`${BASE}/process`, body)
}

export function pluginJobStatus(jobId: string): Promise<PluginJob> {
  return apiFetch<PluginJob>(`${BASE}/jobs/${jobId}`)
}
