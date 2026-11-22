-- One row per area, source series, and interval; exclude demand, shares, and trade.
select
    generation.fact_key,
    generation.geography_id,
    geography.code as geography_code,
    geography.country_code,
    generation.production_type,
    generation.interval_start_utc,
    generation.interval_end_utc,
    generation.interval_minutes,
    generation.original_timestamp,
    generation.source_timezone,
    generation.original_value,
    generation.original_unit,
    generation.value as generation_mw,
    generation.data_status,
    generation.provider,
    generation.dataset,
    generation.raw_sha256
from {{ ref('stg_generation') }} as generation
left join {{ ref('stg_geography') }} as geography
    on generation.geography_id = geography.geography_id
where generation.production_type not in (
    'load', 'cross_border_electricity_trading',
    'renewable_share_of_load', 'renewable_share_of_generation',
    'residual_load', 'hydro_pumped_storage_consumption', 'battery_consumption',
    'hydro_pumped_storage', 'battery'
)
