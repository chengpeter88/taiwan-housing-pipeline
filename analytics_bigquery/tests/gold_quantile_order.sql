-- 分位數必須 p25 <= median <= p75
select 'quarter' as model_name, city_code, district
from {{ ref('agg_district_quarter_price') }}
where not (p25_price_per_ping <= median_price_per_ping and median_price_per_ping <= p75_price_per_ping)
union all
select 'segment', city_code, district
from {{ ref('agg_district_segment_price') }}
where not (p25_price_per_ping <= median_price_per_ping and median_price_per_ping <= p75_price_per_ping)
