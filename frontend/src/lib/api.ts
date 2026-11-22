// Shared HTTP helpers and number formatting for every page.
export const API = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

async function parse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    const detail = typeof payload.detail === 'string' ? payload.detail
      : Array.isArray(payload.detail) ? payload.detail.map((item: { msg?: string; loc?: unknown[] }) => `${item.loc?.slice(-1)[0] ?? 'input'}: ${item.msg}`).join('; ')
        : `Request failed (${response.status})`
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

export async function getJson<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const query = params ? new URLSearchParams(Object.entries(params).filter(([, value]) => value !== undefined && value !== '').map(([key, value]) => [key, String(value)])) : null
  return parse<T>(await fetch(`${API}${path}${query && query.size ? `?${query}` : ''}`))
}

export async function postJson<T>(path: string, body?: unknown): Promise<T> {
  return parse<T>(await fetch(`${API}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) }))
}

export function fmt(value: number | null | undefined, digits = 1) {
  return value == null || !Number.isFinite(value) ? '—' : new Intl.NumberFormat('en-GB', { maximumFractionDigits: digits }).format(value)
}
export function euro(value: number | null | undefined, digits = 0) {
  if (value == null || !Number.isFinite(value)) return '—'
  // Money keeps its decimals (€0.50, not €0.5).
  const text = new Intl.NumberFormat('en-GB', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(Math.abs(value))
  return value < 0 ? `−€${text}` : `€${text}`
}
export function dateTime(value: string | null | undefined) {
  if (!value) return '—'
  const parsed = new Date(value.replace(' ', 'T'))
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }) + ' UTC'
}
export function daysSince(value: string | null | undefined) {
  if (!value) return null
  const parsed = new Date(value.length === 10 ? `${value}T23:59:59Z` : value.replace(' ', 'T'))
  return Number.isNaN(parsed.getTime()) ? null : Math.floor((Date.now() - parsed.getTime()) / 86_400_000)
}
export function titleCase(value: string) {
  return value.replaceAll('_', ' ').replaceAll('-', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}
