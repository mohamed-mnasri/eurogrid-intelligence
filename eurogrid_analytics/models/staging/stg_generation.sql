-- Select standardized generation series while preserving geography and quality metadata.
select
    fact_key,
    geography_id,
    interval_start_utc,
    interval_end_utc,
    interval_minutes,
    original_timestamp,
    source_timezone,
    production_type,
    original_value,
    original_unit,
    value,
    unit,
    data_status,
    provider,
    dataset,
    raw_sha256
from {{ source('eurogrid', 'fct_generation') }}
