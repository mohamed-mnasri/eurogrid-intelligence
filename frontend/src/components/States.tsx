// Loading, empty, error and stale-data states shared by every page (handbook Phase 10, item 7).
import type { ReactNode } from 'react'
import { daysSince } from '../lib/api'

export function Loading({ label }: { label: string }) {
  return <div className="state state-loading" role="status"><span className="spinner" aria-hidden="true" />{label}</div>
}

export function ErrorState({ message, hint = 'Check that the local API (port 8000) and database are running.', onRetry }: { message: string; hint?: string; onRetry?: () => void }) {
  return <div className="state state-error" role="alert"><b>Could not load this view.</b> {message}{hint && <span className="state-hint">{hint}</span>}{onRetry && <button className="button small" onClick={onRetry}>Try again</button>}</div>
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="state state-empty"><b>{title}</b>{children && <span>{children}</span>}</div>
}

export function StatusPill({ status }: { status: string | null | undefined }) {
  const value = status ?? 'unknown'
  return <span className={`pill cap pill-${value.replaceAll(' ', '-').replaceAll('_', '-')}`}>{value.replaceAll('_', ' ')}</span>
}

/** Says how old the newest data is; anything older than a week is flagged as stale. */
export function StaleNotice({ lastDate, what = 'Market data' }: { lastDate: string | null | undefined; what?: string }) {
  const age = daysSince(lastDate)
  if (age == null || age <= 7) return null
  return <div className="state state-stale"><b>Stale data.</b> {what} ends on {lastDate?.slice(0, 10)} ({age} days ago). Run ingestion and dbt to refresh; nothing here is live.</div>
}

export function PageHeader({ eyebrow, title, children, aside }: { eyebrow: string; title: string; children?: ReactNode; aside?: ReactNode }) {
  return <header className="page-header"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1>{children && <p>{children}</p>}</div>{aside}</header>
}
