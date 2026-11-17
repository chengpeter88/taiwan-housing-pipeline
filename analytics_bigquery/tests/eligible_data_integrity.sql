select source_row_key from {{ ref('stg_lvr_sale') }}
where is_price_eligible and (
    transaction_date is null or not is_in_window
    or housing_price is null or housing_price <= 0
    or housing_area is null or housing_area <= 0
    or serial_occurrences <> 1
    or parking_status not in ('no_parking', 'separately_reported')
    or is_special_deal or is_presale_or_separate or not is_housing)
