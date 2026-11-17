select source_row_key
from {{ ref('fct_housing_deal') }}
where housing_area_sqm <= 0 or housing_price_ntd <= 0 or price_per_ping <= 0
    or abs(price_per_ping * housing_area_ping - housing_price_ntd) > numeric '0.1'
