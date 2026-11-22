import { describe, expect, it } from 'vitest'
import { GUIDES } from './guides'

const PAGES = ['overview', 'analytics', 'investigations', 'planner', 'replay', 'operations', 'lab']

describe('page guides', () => {
  it('every page has a purpose, steps and section explanations', () => {
    for (const page of PAGES) {
      const guide = GUIDES[page]
      expect(guide, page).toBeDefined()
      expect(guide.purpose.length, page).toBeGreaterThan(20)
      expect(guide.steps.length, page).toBeGreaterThanOrEqual(3)
      expect(guide.sections.length, page).toBeGreaterThanOrEqual(4)
      for (const section of guide.sections) expect(section.what.length, `${page}: ${section.name}`).toBeGreaterThan(20)
    }
  })
})
