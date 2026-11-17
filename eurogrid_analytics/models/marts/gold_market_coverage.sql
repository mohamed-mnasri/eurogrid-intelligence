-- Build the selector from zones that actually have price facts in the source.
select
    geography.country_code,
    geography.bidding_zone,
    min(price.interval_start_utc) as first_interval_utc,
    max(price.interval_end_utc) as last_interval_utc,
    min(substr(price.original_timestamp, 1, 10)) as first_source_date,
    max(substr(price.original_timestamp, 1, 10)) as last_source_date,
    min(price.source_timezone) as source_timezone,
    min(substr(price.original_timestamp, 1, 10)) as first_source_date,
    max(substr(price.original_timestamp, 1, 10)) as last_source_date,
    min(price.source_timezone) as source_timezone,
    count(*) as price_interval_count,
    count_if(price.data_status = 'valid') as valid_price_interval_count,
    count_if(price.data_status in ('missing', 'invalid', 'stale')) as flagged_price_interval_count,
    min(price.provider) as provider,
    min(price.dataset) as dataset
from {{ ref('stg_market_price') }} as price
left join {{ ref('stg_geography') }} as geography
    on price.geography_id = geography.geography_id
group by geography.country_code, geography.bidding_zone
