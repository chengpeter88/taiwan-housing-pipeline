select 1 as mismatch
where (select count(*) from {{ ref('fct_housing_deal') }})
   <> (select coalesce(sum(deal_count), 0) from {{ ref('agg_district_month_price') }})
or (select count(*) from {{ ref('stg_lvr_sale') }})
   <> (select coalesce(sum(row_count), 0) from {{ ref('qa_exclusions') }})
