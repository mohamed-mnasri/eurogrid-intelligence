-- One row per coordinate weather location, variable, and UTC interval.
select
    weather.fact_key,
    weather.geography_id,
    geography.country_code,
    geography.latitude,
    geography.longitude,
    weather.interval_start_utc,
    weather.interval_end_utc,
    weather.interval_minutes,
    weather.original_timestamp,
    weather.source_timezone,
    weather.variable,
    weather.original_value,
    weather.original_unit,
    weather.value as weather_value,
    weather.unit as weather_unit,
    weather.data_status,
    weather.provider,
    weather.dataset,
    weather.raw_sha256
from {{ ref('stg_weather') }} as weather
left join {{ ref('stg_geography') }} as geography
    on weather.geography_id = geography.geography_id
