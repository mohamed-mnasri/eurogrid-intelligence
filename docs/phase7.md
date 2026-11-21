# Phase 7: Renewable simulation engine

Phase 7 is a tested Python engine that simulates a household's solar PV and battery hour by hour. It then calculates bills, savings, payback, cash flow, NPV and sensitivity cases. No AI is involved. The same inputs always give the same outputs.

## The pieces

| File | What it does |
|---|---|
| `eurogrid/simulation_models.py` | Pydantic scenario schema and validation: location, PV, battery, system costs, fixed/dynamic tariff, finance assumptions. Also `default_systems()`, the three example configurations. |
| `eurogrid/load_profile.py` | Hourly household load. A **synthetic** profile (labelled as such) is used when no meter data is supplied. |
| `eurogrid/pv.py` | Hourly PV production from **PVGIS** (cached through the Phase 2 raw store) or from **recent Open-Meteo weather** using a documented model. |
| `eurogrid/simulation.py` | The energy balance, battery, tariffs, finance and sensitivity analysis. |
| `eurogrid/simulate_cli.py` | Terminal command to run a scenario. |

## Energy balance (every hour, kWh)

```
solar_direct = min(pv, load)
surplus      = pv − solar_direct        shortfall = load − solar_direct
charge       ≤ surplus, ≤ max_charge_kw, ≤ (max_soc − soc) / η_charge
discharge    ≤ shortfall, ≤ max_discharge_kw, ≤ (soc − min_soc) × η_discharge
soc_next     = soc + charge × η_charge − discharge / η_discharge
grid_import  = shortfall − discharge    grid_export = surplus − charge
loss         = charge × (1 − η_charge) + discharge × (1/η_discharge − 1)
```

The battery charges only from solar surplus and discharges only to cover the home's load. It never does both in the same hour. Each hour is checked:

```
pv + grid_import + soc_before = load + grid_export + loss + soc_after
```

The run fails if any hour is off by more than 0.000001 kWh. Real runs are off by about 1e-15 kWh.

## PV production

- **PVGIS** (default): the official hourly `seriescalc` series for the location, tilt, orientation and system loss. Each request is stored once under `data/raw/` and cached. PVGIS hourly data covers 2005–2023.
- **Weather model** (for 2024 onwards, or to match wholesale-price years): Open-Meteo hourly sunlight and temperature are converted to electricity in four steps:
  1. NOAA sun position.
  2. The Erbs split between direct and diffuse light.
  3. Isotropic tilt conversion with 20% ground reflection.
  4. A cell-temperature correction (NOCT 45 °C, −0.4%/°C), then the system loss.

  Open-Meteo stamps each value at the *end* of its hour, which the model accounts for.
- **Check against PVGIS:** Berlin 2023, 1 kWp, 35° south-facing, 14% loss gave 1,002 kWh from PVGIS and 1,064 kWh from the weather model (+6.2%). Run `--compare-pv` to check your own site.
- A PVGIS annual total is never spread evenly over the year's hours.

## Tariffs

- **Fixed:** your own import and export rates. The import price does not change with wholesale prices.
- **Dynamic (illustrative):** `(wholesale/1000 + supplier markup + grid charges + taxes/levies) × (1 + VAT)`, using stored day-ahead prices for the chosen bidding zone.
  - A negative wholesale price passes through unless `floor_energy_price_at_zero` is set.
  - Export compensation is a separate explicit rate; it is never inferred from wholesale prices.
  - If any hour's wholesale price is missing, the run stops instead of guessing.

## Money

| Output | How it is calculated |
|---|---|
| Annual net savings | bill without the system − bill with it − maintenance |
| Simple payback | CAPEX ÷ first-year net savings; shown as "none" when savings ≤ 0 |
| Cash flow | each year's savings with PV degradation and price escalation applied, minus maintenance and any battery replacement |
| NPV | −CAPEX + Σ yearly net cash flow ÷ (1 + rate)^year |
| Sensitivity | CAPEX ±20%, import price ±20%, export rate ±50%, solar yield ±10%, degradation 0% / 1%, discount rate ±2 points |

Year-by-year savings scale the first year's avoided-import cost and export revenue by degradation and escalation. This is a documented approximation; the engine does not re-run the simulation for every year.

## The three default configurations

| ID | System | Illustrative CAPEX |
|---|---|---|
| `pv-4kw` | 4 kWp PV | €6,000 |
| `pv-4kw-bat-5kwh` | 4 kWp PV + 5 kWh battery (2.5 kW) | €10,000 |
| `pv-8kw-bat-10kwh` | 8 kWp PV + 10 kWh battery (5 kW) | €18,000 |

These costs are placeholders. Results say so, and you should replace them with a real quote.

Example: Berlin 2023, PVGIS, 3,500 kWh/year synthetic load, €0.36 import, €0.08 export:

| System | PV kWh | Self-use | Self-sufficiency | Savings/yr | Payback | NPV (20 y, 4%) |
|---|---|---|---|---|---|---|
| 4 kWp | 4,008 | 30.4% | 34.8% | €561 | 10.7 y | €2,302 |
| 4 kWp + 5 kWh | 4,008 | 57.0% | 62.3% | €793 | 12.6 y | €565 |
| 8 kWp + 10 kWh | 8,016 | 36.4% | 79.3% | €1,227 | 14.7 y | −€2,541 |

High self-sufficiency does not automatically mean the best return; compare NPV and payback against your own goal.

## Try it

```powershell
.\.venv\Scripts\python.exe -m eurogrid.simulate_cli --lat 52.52 --lon 13.405 --annual-kwh 3500 --year 2023 --compare-pv
```

```powershell
.\.venv\Scripts\python.exe -m eurogrid.simulate_cli --lat 52.52 --lon 13.405 --annual-kwh 3500 --year 2025 --pv-source weather_model --dynamic-zone DE-LU
```

The first run downloads PVGIS/Open-Meteo data once. Results are saved to `data/simulations/<scenario>.json`, which is gitignored.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_simulation -v
```

The tests cover:

- hand-calculated battery steps;
- capacity and minimum-charge limits;
- energy conservation and power limits on 2,000 random hours;
- the dynamic-tariff formula and refusal to fill missing wholesale hours;
- a hand-calculated NPV and payback;
- battery replacement, degradation and escalation;
- three reproducible configurations;
- input validation;
- synthetic load totals;
- sun position at a known instant;
- PVGIS parsing and caching (one request served twice), with an offline fake client.

## Limitations

- No grid charging, export limits or time-of-use battery strategy yet; the battery maximises self-consumption.
- The synthetic load profile is a constructed shape, not a measured household or published standard profile.
- The weather model uses horizontal irradiance at one point with no shading or horizon.
- Tax incentives, financing costs and feed-in tariff expiry are not modelled.
