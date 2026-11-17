-- Exact median over individual residential transactions, not monthly medians.
select distinct city_code, district, transaction_month,
    count(*) over district_month as deal_count,
    percentile_cont(price_per_ping, 0.5) over district_month as median_price_per_ping
from {{ ref('fct_housing_deal') }}
where is_residential
window district_month as (partition by city_code, district, transaction_month)
