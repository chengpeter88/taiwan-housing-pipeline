select source_row_key, source_sha256, city_code, district, serial_no,
    transaction_date, date_trunc(transaction_date, month) as transaction_month,
    main_use, building_type, is_residential, parking_status,
    housing_price as housing_price_ntd, housing_area as housing_area_sqm,
    housing_area / numeric '3.30579' as housing_area_ping,
    safe_divide(housing_price, housing_area) * numeric '3.30579' as price_per_ping
from {{ ref('stg_lvr_sale') }}
where is_price_eligible
