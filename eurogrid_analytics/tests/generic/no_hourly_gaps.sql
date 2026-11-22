{#
  Fails for every group (bidding zone or country) that is missing whole hours between its first and last
  interval. Schema tests (unique, not null, accepted values) cannot see rows that are absent; this one can.
  Sub-hourly data counts an hour as present if any interval starts in it.
#}
{% test no_hourly_gaps(model, column_name, timestamp_column='interval_start_utc') %}

with hours as (
    select {{ column_name }} as grp, date_trunc('hour', {{ timestamp_column }}) as hour_start
    from {{ model }}
    where {{ column_name }} is not null
    group by all
),

bounds as (
    select grp, min(hour_start) as first_hour, max(hour_start) as last_hour, count(*) as present_hours
    from hours
    group by grp
)

select
    grp,
    first_hour,
    last_hour,
    present_hours,
    cast((epoch(last_hour) - epoch(first_hour)) / 3600 + 1 as bigint) - present_hours as missing_hours
from bounds
where cast((epoch(last_hour) - epoch(first_hour)) / 3600 + 1 as bigint) > present_hours

{% endtest %}
