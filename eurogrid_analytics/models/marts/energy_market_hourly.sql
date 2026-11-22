-- Exploratory wide join; grain is price interval x generation series x weather variable.
with prices as (
    select *
    from {{ ref('stg_market_price') }}
),

generation as (
    select
        geography_id,
        interval_start_utc,
        production_type,
        value as generation_mw,
        data_status as generation_status
    from {{ ref('stg_generation') }}
),

weather as (
    select
        geography_id,
        interval_start_utc,
        variable,
        value as weather_value,
        unit as weather_unit,
        data_status as weather_status
    from {{ ref('stg_weather') }}
),

price_geography as (
    select
        price.*,
        geography.geography_type,
        geography.code as geography_code,
        geography.country_code,
        geography.bidding_zone
    from prices as price
    left join {{ ref('stg_geography') }} as geography
        on price.geography_id = geography.geography_id
)

select
    price_geography.fact_key as price_fact_key,
    price_geography.geography_id,
    price_geography.geography_type,
    price_geography.geography_code,
    price_geography.country_code,
    price_geography.bidding_zone,
    price_geography.interval_start_utc,
    price_geography.interval_end_utc,
    price_geography.interval_minutes,
    price_geography.value as price_eur_per_mwh,
    price_geography.unit as price_unit,
    price_geography.data_status as price_status,
    generation.production_type,
    generation.geography_id as generation_geography_id,
    generation.generation_mw,
    generation.generation_status,
    weather.geography_id as weather_geography_id,
    weather.variable as weather_variable,
    weather.weather_value,
    weather.weather_unit,
    weather.weather_status
from price_geography
left join generation
    on price_geography.interval_start_utc = generation.interval_start_utc
left join weather
    on price_geography.interval_start_utc = weather.interval_start_utc
