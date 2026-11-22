-- One row per normalized day-ahead price interval and bidding zone.
select
    price.fact_key,
    price.geography_id,
    geography.country_code,
    geography.bidding_zone,
    price.interval_start_utc,
    price.interval_end_utc,
    price.interval_minutes,
    price.original_timestamp,
    price.source_timezone,
    price.original_value,
    price.original_unit,
    price.value as price_eur_per_mwh,
    price.data_status,
    price.provider,
    price.dataset,
    price.raw_sha256
from {{ ref('stg_market_price') }} as price
left join {{ ref('stg_geography') }} as geography
    on price.geography_id = geography.geography_id
