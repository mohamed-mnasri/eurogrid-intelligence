// Household planner types, location presets and the objective-based recommendation.
export type SystemConfig = { system_id: string; label: string; capex_eur: number; annual_maintenance_eur: number; cost_source: string; pv: { peak_kw: number; tilt_deg: number; azimuth_deg: number }; battery: { usable_capacity_kwh: number } | null }
export type Cashflow = { year: number; cumulative_cashflow_eur: number; net_cashflow_eur: number }
export type SystemResult = {
  system_id: string; label: string; pv_peak_kw: number; battery_kwh: number | null; cost_source: string
  energy: { load_kwh: number; pv_kwh: number; solar_direct_kwh: number; grid_import_kwh: number; grid_export_kwh: number; battery_discharge_kwh: number; self_consumption_pct: number | null; self_sufficiency_pct: number | null; battery_equivalent_full_cycles: number | null }
  financial: { capex_eur: number; bill_without_system_eur: number; bill_with_system_eur: number; annual_net_savings_eur: number; simple_payback_years: number | null; payback_note: string; npv_eur: number; cashflow: Cashflow[] }
  sensitivity: { case: string; npv_eur: number; simple_payback_years: number | null }[]
}
export type ScenarioResult = { scenario_id: string; simulation_year: number; load_profile_source: string; pv_source: Record<string, unknown>; tariff_note: string; systems: SystemResult[]; labels: string[] }
export type Month = { month: string; load_kwh: number; pv_kwh: number; solar_direct_kwh: number; battery_discharge_kwh: number; grid_import_kwh: number; grid_export_kwh: number; import_cost_eur: number; export_revenue_eur: number }
export type Hour = { interval_start_utc: string; load_kwh: number; pv_kwh: number; battery_charge_kwh: number; battery_discharge_kwh: number; soc_end_kwh: number; grid_import_kwh: number; grid_export_kwh: number; import_price_eur_per_kwh: number }

export const LOCATIONS: Record<string, { label: string; latitude: number; longitude: number; timezone: string; zone: string; household: number; retail: number }> = {
  berlin: { label: 'Berlin, Germany', latitude: 52.52, longitude: 13.405, timezone: 'Europe/Berlin', zone: 'DE-LU', household: 3500, retail: 0.39 },
  paris: { label: 'Paris, France', latitude: 48.857, longitude: 2.352, timezone: 'Europe/Paris', zone: 'FR', household: 4500, retail: 0.25 },
  madrid: { label: 'Madrid, Spain', latitude: 40.417, longitude: -3.704, timezone: 'Europe/Madrid', zone: 'ES', household: 3300, retail: 0.24 },
  amsterdam: { label: 'Amsterdam, Netherlands', latitude: 52.37, longitude: 4.895, timezone: 'Europe/Amsterdam', zone: 'NL', household: 2800, retail: 0.30 },
  warsaw: { label: 'Warsaw, Poland', latitude: 52.23, longitude: 21.012, timezone: 'Europe/Warsaw', zone: 'PL', household: 2200, retail: 0.20 },
}
export const OBJECTIVES = { npv: 'Highest NPV', payback: 'Shortest payback', independence: 'Most energy independence', budget: 'Best NPV within budget' } as const
export type Objective = keyof typeof OBJECTIVES

export function pickBest(systems: SystemResult[], objective: Objective, budget: number) {
  const pool = objective === 'budget' ? systems.filter((item) => item.financial.capex_eur <= budget) : systems
  if (!pool.length) return null
  const score = (item: SystemResult) => objective === 'payback' ? -(item.financial.simple_payback_years ?? Infinity)
    : objective === 'independence' ? item.energy.self_sufficiency_pct ?? 0 : item.financial.npv_eur
  return pool.reduce((best, item) => score(item) > score(best) ? item : best)
}

