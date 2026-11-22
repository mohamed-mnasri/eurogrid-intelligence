-- Hour-of-day benchmark over the observations currently stored for each zone.
select
    price.geography_id,
    geography.country_code,
    geography.bidding_zone,
    extract(hour from timezone('UTC', price.interval_start_utc))::integer as utc_hour,
    avg(price.value) as mean_price_eur_per_mwh,
    count(price.value) as baseline_observation_count
from {{ ref('stg_market_price') }} as price
left join {{ ref('stg_geography') }} as geography
    on price.geography_id = geography.geography_id
where price.data_status = 'valid'
group by
    price.geography_id, geography.country_code, geography.bidding_zone,
    extract(hour from timezone('UTC', price.interval_start_utc))
