-- Public-power demand is MW at the interval, not energy consumed during it.
select
    demand.fact_key,
    demand.geography_id,
    geography.code as geography_code,
    geography.country_code,
    demand.interval_start_utc,
    demand.interval_end_utc,
    demand.interval_minutes,
    demand.original_timestamp,
    demand.source_timezone,
    demand.original_value,
    demand.original_unit,
    demand.value as demand_mw,
    demand.data_status,
    demand.provider,
    demand.dataset,
    demand.raw_sha256
from {{ ref('stg_generation') }} as demand
left join {{ ref('stg_geography') }} as geography
    on demand.geography_id = geography.geography_id
where demand.production_type = 'load'
