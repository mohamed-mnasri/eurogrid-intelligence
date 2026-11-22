import { describe, expect, it } from 'vitest'
import { pickBest, type SystemResult } from './household'

function system(id: string, capex: number, npv: number, payback: number | null, sufficiency: number): SystemResult {
  return {
    system_id: id, label: id, pv_peak_kw: 4, battery_kwh: null, cost_source: 'test',
    energy: { load_kwh: 3500, pv_kwh: 4000, solar_direct_kwh: 1200, grid_import_kwh: 2300, grid_export_kwh: 2800, battery_discharge_kwh: 0, self_consumption_pct: 30, self_sufficiency_pct: sufficiency, battery_equivalent_full_cycles: null },
    financial: { capex_eur: capex, bill_without_system_eur: 1365, bill_with_system_eur: 667, annual_net_savings_eur: 600, simple_payback_years: payback, payback_note: '', npv_eur: npv, cashflow: [] },
    sensitivity: [],
  }
}

const systems = [system('pv', 6000, 2500, 10, 35), system('pv-bat', 10000, 1800, 12, 62), system('big', 18000, -500, null, 79)]

describe('pickBest', () => {
  it('picks by NPV, payback and independence', () => {
    expect(pickBest(systems, 'npv', 0)?.system_id).toBe('pv')
    expect(pickBest(systems, 'payback', 0)?.system_id).toBe('pv')
    expect(pickBest(systems, 'independence', 0)?.system_id).toBe('big')
  })
  it('treats a missing payback as never paying back', () => {
    expect(pickBest([system('big', 18000, -500, null, 79), system('pv-bat', 10000, 1800, 12, 62)], 'payback', 0)?.system_id).toBe('pv-bat')
  })
  it('respects the budget and returns nothing when no system fits', () => {
    expect(pickBest(systems, 'budget', 12000)?.system_id).toBe('pv')
    expect(pickBest(systems, 'budget', 5000)).toBeNull()
  })
})
