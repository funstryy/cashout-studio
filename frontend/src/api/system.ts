import { apiFetch } from './http'

export interface GpuStats {
  name: string | null
  driver: string | null
  /** Summed across GPU engines, clamped to 100. Null when unreadable. */
  utilisation: number | null
  vram_used_bytes: number | null
  vram_total_bytes: number | null
  vram_percent: number | null
}

export interface SystemSnapshot {
  gpu: GpuStats
  cpu_percent: number | null
  ram_used_bytes: number | null
  ram_total_bytes: number | null
  disk_free_bytes: number | null
  disk_total_bytes: number | null
  disk_path: string | null
  measured: boolean
}

/** Every field can be null, and the UI must render that as "unknown" rather
 *  than zero - a GPU ring sitting at 0% because a counter failed is a lie
 *  that looks exactly like an idle machine. */
export function systemSnapshot(): Promise<SystemSnapshot> {
  return apiFetch<SystemSnapshot>('/api/system')
}
