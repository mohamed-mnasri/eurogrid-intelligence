-- Select standardized weather measurements; retain the coordinate geography key.
select
    fact_key,
    geography_id,
    interval_start_utc,
    interval_end_utc,
    interval_minutes,
    original_timestamp,
    source_timezone,
    variable,
    original_value,
    original_unit,
    value,
    unit,
    data_status,
    provider,
    dataset,
    raw_sha256
from {{ source('eurogrid', 'fct_weather') }}
