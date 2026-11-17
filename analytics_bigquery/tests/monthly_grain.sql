select city_code, district, transaction_month
from {{ ref('agg_district_month_price') }}
group by 1, 2, 3 having count(*) > 1
