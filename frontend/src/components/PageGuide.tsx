// "How to use this page" panel. Opens automatically on a person's first visit to a page; can be hidden and reopened.
// The open/closed choice is a per-browser convenience kept in localStorage (wrapped so a blocked storage never breaks the page).
// A link can force it closed with ?guide=closed (used for README screenshots).
import { useState } from 'react'
import { GUIDES } from '../lib/guides'

function storedOpen(page: string): boolean {
  if (new URLSearchParams(window.location.search).get('guide') === 'closed') return false
  try { return window.localStorage.getItem(`eg-guide-${page}`) !== 'hidden' } catch { return true }
}

function remember(page: string, open: boolean) {
  try { window.localStorage.setItem(`eg-guide-${page}`, open ? 'shown' : 'hidden') } catch { /* storage unavailable */ }
}

export function PageGuide({ page }: { page: string }) {
  const guide = GUIDES[page]
  const [open, setOpen] = useState(() => storedOpen(page))
  if (!guide) return null
  const toggle = (next: boolean) => { setOpen(next); remember(page, next) }

  if (!open) {
    return <button className="guide-toggle" onClick={() => toggle(true)} aria-expanded="false">
      <span aria-hidden="true">?</span> How to use this page
    </button>
  }
  return <section className="page-guide" aria-label="How to use this page">
    <div className="guide-head">
      <div><span className="guide-badge" aria-hidden="true">?</span><b>How to use this page</b></div>
      <button className="button small" onClick={() => toggle(false)} aria-expanded="true">Hide guide</button>
    </div>
    <p className="guide-purpose">{guide.purpose}</p>
    <div className="guide-grid">
      <div>
        <h3>Steps</h3>
        <ol>{guide.steps.map((step) => <li key={step}>{step}</li>)}</ol>
        {guide.terms && <>
          <h3>Words you'll see</h3>
          <dl>{guide.terms.map((item) => <div key={item.term}><dt>{item.term}</dt><dd>{item.meaning}</dd></div>)}</dl>
        </>}
      </div>
      <div>
        <h3>What each section shows</h3>
        <dl>{guide.sections.map((item) => <div key={item.name}><dt>{item.name}</dt><dd>{item.what}</dd></div>)}</dl>
      </div>
    </div>
  </section>
}
