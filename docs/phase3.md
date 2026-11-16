# Phase 3: Normalization and DuckDB

The Bronze JSON remains unchanged. `python -m eurogrid.normalize` reads saved sample wrappers (both the Phase 1 sample directory and the Phase 2 partition layout) and loads the price, public-generation and historical-weather source set into `data/eurogrid.duckdb`. Raw files and the database are local and gitignored.

## Model

- `dim_geography` keeps country, bidding zone, Europe aggregate and coordinate locations distinct.
- `fct_market_price` stores day-ahead prices as EUR/MWh; `fct_generation` stores production-series values as MW; `fct_weather` stores temperature in °C, wind speed in m/s, and shortwave radiation in W/m².
- Each fact preserves source timestamp, source timezone/offset, original numeric value/unit, normalized value/unit, interval start/end in UTC, interval length, provider, dataset, raw-file SHA-256 and a data-quality status.
- Stable natural fact hashes make reruns replace a matching observation while leaving distinct versions of the raw JSON in Bronze.
- The database exposes `stg_market_price`, `stg_generation`, `stg_weather`, and `vw_market_weather_aligned` for initial inspection.

For timestamp strings with UTC offsets, the offset is honored. Naive local times use the source IANA timezone; a repeated fall-back hour is assigned to the next valid UTC instant, and a nonexistent spring-forward hour is marked invalid by the parser and excluded from timestamped facts. Windows installs need the `tzdata` dependency for IANA rules. Missing, zero, invalid/non-finite, and explicitly stale values have distinct handling; a valid zero stays zero.

## Build and inspect

```powershell
python -m pip install -r requirements.txt
python -m eurogrid.normalize
python -c "import duckdb; c=duckdb.connect('data/eurogrid.duckdb', read_only=True); print(c.execute('select count(*) from fct_market_price').fetchone()); print(c.execute('select count(*) from vw_market_weather_aligned where weather_value is not null').fetchone())"
```

To load a different raw location or database file, pass `--raw` and `--database`. Loading is transactional and repeatable. The current Phase 1 sample has 48 market intervals, 960 generation facts and 144 weather facts. Its source date windows do not have identical UTC endpoints, so the raw sample's direct join has 141 rows; the normalization tests separately verify a full aligned ordinary day across all three weather variables and include 23-hour and 25-hour Europe/Berlin DST days.

```powershell
python -m unittest discover -s tests -v
```

Eurostat JSON-stat prices, PVGIS yield estimates, and energy-market facts beyond the sampled providers are still retained in Bronze only; they are not silently projected into the hourly power/weather fact tables.
