-- Retain the provider's signed net scheduled-trading series as reported (MW).
select
    flow.fact_key,
    flow.geography_id,
    geography.code as geography_code,
    geography.country_code,
    flow.interval_start_utc,
    flow.interval_end_utc,
    flow.interval_minutes,
    flow.original_timestamp,
    flow.source_timezone,
    flow.original_value,
    flow.original_unit,
    flow.value as net_cross_border_trading_mw,
    flow.data_status,
    flow.provider,
    flow.dataset,
    flow.raw_sha256
from {{ ref('stg_generation') }} as flow
left join {{ ref('stg_geography') }} as geography
    on flow.geography_id = geography.geography_id
where flow.production_type = 'cross_border_electricity_trading'
