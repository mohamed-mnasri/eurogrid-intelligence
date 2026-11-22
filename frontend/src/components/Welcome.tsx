// One-time welcome for first-time visitors: what the app is and a suggested order through the pages.
import { useState } from 'react'

const KEY = 'eg-welcome'

function seen(): boolean {
  if (new URLSearchParams(window.location.search).get('guide') === 'closed') return true
  try { return window.localStorage.getItem(KEY) === 'seen' } catch { return false }
}

const TOUR = [
  { href: '#/overview', label: 'Market Overview', text: 'what electricity cost and how much was used in a month' },
  { href: '#/analytics', label: 'Market Analytics', text: 'compare countries and find unusual price hours' },
  { href: '#/investigations', label: 'AI Investigations', text: 'what was happening during an unusual hour, with evidence' },
  { href: '#/planner', label: 'Renewable Planner', text: 'would solar and a battery pay off for a home' },
  { href: '#/replay', label: 'Event Replay', text: 'what one price event cost that home' },
]

export function Welcome() {
  const [open, setOpen] = useState(() => !seen())
  if (!open) return null
  const close = () => { setOpen(false); try { window.localStorage.setItem(KEY, 'seen') } catch { /* storage unavailable */ } }
  return <section className="welcome" aria-label="Welcome">
    <div className="guide-head"><div><span className="guide-badge" aria-hidden="true">i</span><b>Welcome to EuroGrid</b></div>
      <button className="button small" onClick={close}>Got it</button></div>
    <p>This app turns public European electricity data into answers: what prices did, why a price suddenly jumped, and what it means for a home with solar panels. Every page has a <b>How to use this page</b> guide at the top. A good first tour:</p>
    <ol>{TOUR.map((item) => <li key={item.href}><a href={item.href}>{item.label}</a>: {item.text}.</li>)}</ol>
    <p className="muted">Data Operations and Scenario Lab are for checking data quality and testing the investigators.</p>
  </section>
}
