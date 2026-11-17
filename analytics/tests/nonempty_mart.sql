select 1 as empty_mart where not exists (select 1 from {{ ref('fct_housing_deal') }})
