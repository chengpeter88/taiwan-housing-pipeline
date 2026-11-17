select city_code, district, serial_no, batch_id, transaction_date,
       cast(date_trunc('month', transaction_date) as date) as transaction_month,
       housing_price_ntd, housing_area_sqm,
       housing_area_sqm / 3.30579 as housing_area_ping,
       housing_price_ntd / housing_area_sqm * 3.30579 as price_per_ping
from {{ ref('stg_lvr_sale') }}
where exclusion_reason = 'eligible'
