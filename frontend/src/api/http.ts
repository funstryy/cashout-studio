export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

async function parseErrorBody(resp: Response): Promise<string> {
  try {
    const data = await resp.clone().json()
    if (typeof data?.error === 'string') return data.error
    if (typeof data?.detail === 'string') return data.detail
    if (typeof data?.error?.message === 'string') return data.error.message
    return JSON.stringify(data)
  } catch {
    return resp.statusText || `HTTP ${resp.status}`
  }
}

/** The active profile, read lazily to avoid an import cycle with users.ts.
 * Every request carries it so the backend can scope history to one person. */
function userHeader(): Record<string, string> {
  try {
    const id = localStorage.getItem('cashout_studio_user_id')
    return id ? { 'X-User-Id': id } : {}
  } catch {
    return {}
  }
}

export async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    ...init,
    headers: { ...(init?.headers as Record<string, string> | undefined), ...userHeader() },
  })
  if (!resp.ok) {
    throw new ApiError(await parseErrorBody(resp), resp.status)
  }
  const text = await resp.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export function apiJson<T>(url: string, body: unknown, method = 'POST'): Promise<T> {
  return apiFetch<T>(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** True when the request failed because the backing model process isn't running. */
export function isModelInactive(err: unknown): boolean {
  return err instanceof ApiError && err.status === 503
}
