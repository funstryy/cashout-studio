import { apiFetch, apiJson } from './http'

const BASE = '/api/spotify'

export interface SpotifyStatus {
  configured: boolean
  connected: boolean
  display_name: string | null
  client_id: string | null
  scopes: string[]
  redirect_path: string
}

export interface ListeningTrack {
  track_id: string
  title: string
  artist: string
  artists: string[]
  album: string
  year: string
  genres: string[]
  plays: number
  rank: number | null
}

export interface LocalAudio {
  path: string
  label: string
  source: 'library' | 'folder'
}

export interface MatchedTrack extends ListeningTrack {
  local: LocalAudio
  score: number
}

export interface MissingTrack extends ListeningTrack {
  best_score: number
}

/** Whether genres arrived at all. Spotify removed the batch artist endpoint
 *  in February 2026 and may refuse the single one too, so this is reported
 *  rather than assumed - captions are much weaker without genres, and the
 *  user should know which they got. */
export type GenreStatus = 'ok' | 'partial' | 'unavailable' | 'skipped'

export type ListeningSource = 'recent' | 'top_short' | 'top_medium' | 'top_long' | 'saved'

export function spotifyStatus(): Promise<SpotifyStatus> {
  return apiFetch<SpotifyStatus>(`${BASE}/status`)
}

export function saveClientId(clientId: string): Promise<SpotifyStatus> {
  return apiJson<SpotifyStatus>(`${BASE}/config`, { client_id: clientId })
}

/** Starts the sign-in. The backend opens the system browser; the returned URL
 *  is a fallback for when it could not. */
export function spotifyLogin(port: number): Promise<{ url: string; redirect_uri: string; opened: boolean }> {
  return apiFetch(`${BASE}/login?port=${port}`)
}

export function spotifyDisconnect(): Promise<SpotifyStatus> {
  return apiJson<SpotifyStatus>(`${BASE}/disconnect`, {})
}

export function fetchListening(
  source: ListeningSource,
  withGenres = true,
): Promise<{ source: string; genre_status: GenreStatus; tracks: ListeningTrack[] }> {
  return apiFetch(`${BASE}/listening?source=${source}&with_genres=${withGenres}`)
}

export function matchTracks(body: {
  tracks: ListeningTrack[]
  folders?: string[]
  include_library?: boolean
  threshold?: number
}): Promise<{ matched: MatchedTrack[]; missing: MissingTrack[]; searched: number; threshold: number }> {
  return apiJson(`${BASE}/match`, body)
}

export function buildDataset(body: {
  dataset_name: string
  matched: MatchedTrack[]
  trigger?: string
  include_artist?: boolean
  style_tags?: string[]
}): Promise<{
  dataset_name: string
  audio_dir: string
  absolute_dir: string
  files: { file: string; caption: string; title: string }[]
}> {
  return apiJson(`${BASE}/build-dataset`, body)
}

export function datasetCaptions(datasetName: string): Promise<{ captions: Record<string, string> }> {
  return apiFetch(`${BASE}/captions?dataset_name=${encodeURIComponent(datasetName)}`)
}
