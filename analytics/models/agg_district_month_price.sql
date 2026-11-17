select city_code, district, transaction_month, count(*) as deal_count,
       median(price_per_ping) as median_price_per_ping
from {{ ref('fct_housing_deal') }}
group by city_code, district, transaction_month
