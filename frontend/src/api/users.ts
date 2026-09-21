import { apiFetch, apiJson } from './http'

export interface UserProfile {
  id: number
  name: string
  created_at: string
}

const STORAGE_KEY = 'cashout_studio_user_id'

/** The chosen profile, remembered per machine. Read synchronously because
 * every API call needs it in a header before anything renders. */
export function currentUserId(): number | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? Number(raw) : null
  } catch {
    return null
  }
}

export function setCurrentUserId(id: number | null): void {
  try {
    if (id == null) localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, String(id))
  } catch {
    // Private browsing - the choice just won't survive a reload.
  }
}

export async function listUsers(): Promise<{ users: UserProfile[]; default_id: number }> {
  return apiFetch<{ users: UserProfile[]; default_id: number }>('/api/users')
}

export function createUser(name: string): Promise<UserProfile> {
  return apiJson<UserProfile>('/api/users', { name })
}

export async function deleteUser(id: number): Promise<void> {
  await apiFetch(`/api/users/${id}`, { method: 'DELETE' })
}
