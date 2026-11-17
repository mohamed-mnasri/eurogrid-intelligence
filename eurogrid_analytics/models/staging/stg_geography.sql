select
    geography_id,
    geography_type,
    code,
    name,
    country_code,
    bidding_zone,
    latitude,
    longitude,
    source
from {{ source('eurogrid', 'dim_geography') }}