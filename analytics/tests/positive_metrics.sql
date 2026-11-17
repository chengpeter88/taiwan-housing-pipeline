select * from {{ ref('fct_housing_deal') }}
where price_per_ping <= 0 or housing_area_sqm <= 0 or housing_price_ntd <= 0
   or transaction_date > cast('{{ var("as_of_date") }}' as date)
