import { apiFetch, apiJson } from './http'
import type { TimelineProject } from '../audio/timelineTypes'

export interface CollabPeer {
  peer_id: string
  name: string
  colour: string
  is_host: boolean
  playhead: number
}

export interface CollabState {
  active: boolean
  project_name: string
  peers: CollabPeer[]
  revision: number
  token: string
  port: number
  addresses: string[]
  listening_on: string
  /** `address:port#token` - the one thing a host sends a friend. */
  code: string
}

export function collabStatus(): Promise<CollabState> {
  return apiFetch<CollabState>('/api/collab/status')
}

export function startHosting(
  project: TimelineProject,
  projectName: string,
  address?: string,
): Promise<CollabState> {
  return apiJson<CollabState>('/api/collab/host', {
    project,
    project_name: projectName,
    address,
  })
}

export function stopHosting(): Promise<CollabState> {
  return apiJson<CollabState>('/api/collab/stop', {})
}

export interface InviteParts {
  host: string
  port: number
  token: string
}

/**
 * Reads an invite the way a person will actually have it: pasted from a chat
 * window, possibly with a scheme on the front or a stray space on the end.
 */
export function parseInvite(raw: string): InviteParts | null {
  const text = raw.trim().replace(/^wss?:\/\//i, '').replace(/^https?:\/\//i, '')
  const match = /^([^\s/:#]+)(?::(\d+))?#(.+)$/.exec(text)
  if (!match) return null
  const token = match[3].trim()
  if (!token) return null
  return { host: match[1], port: match[2] ? Number(match[2]) : 9000, token }
}

export function socketUrl(parts: InviteParts, name: string, asHost: boolean): string {
  const query = new URLSearchParams({
    token: parts.token,
    name,
    host: asHost ? '1' : '0',
  })
  return `ws://${parts.host}:${parts.port}/api/collab/ws?${query.toString()}`
}

/**
 * Points a clip at whoever owns it.
 *
 * A shared arrangement is full of relative URLs like `/api/tracks/7/audio`,
 * and on a guest's machine track 7 is some unrelated song of their own. So
 * every source is rewritten to an absolute URL against the host, carrying
 * the session token - the host's guard refuses audio without it.
 *
 * The host itself passes `null` and gets the URLs back untouched, which is
 * what keeps one code path for both sides.
 */
export function resolveSource(url: string, remote: InviteParts | null): string {
  if (!remote || !url.startsWith('/')) return url
  const separator = url.includes('?') ? '&' : '?'
  return `http://${remote.host}:${remote.port}${url}${separator}ct=${encodeURIComponent(remote.token)}`
}
