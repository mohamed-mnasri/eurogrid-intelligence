import { describe, expect, it } from 'vitest'
import { daysSince, euro, fmt, titleCase } from './api'

describe('formatting', () => {
  it('shows missing values as a dash instead of zero', () => {
    expect(fmt(null)).toBe('—')
    expect(fmt(Number.NaN)).toBe('—')
    expect(euro(undefined)).toBe('—')
  })
  it('formats numbers and signed euros', () => {
    expect(fmt(1234.567, 1)).toBe('1,234.6')
    expect(euro(-1.5, 2)).toBe('−€1.5')
    expect(euro(1.49, 2)).toBe('€1.49')
  })
  it('turns identifiers into labels', () => {
    expect(titleCase('cross_border_flows')).toBe('Cross Border Flows')
  })
  it('counts days since a date and ignores invalid input', () => {
    const tenDaysAgo = new Date(Date.now() - 10 * 86_400_000).toISOString()
    expect(daysSince(tenDaysAgo)).toBe(10)
    expect(daysSince('not a date')).toBeNull()
    expect(daysSince(null)).toBeNull()
  })
})
