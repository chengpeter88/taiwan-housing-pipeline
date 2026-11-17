-- 每張 gold 彙總表的 grain 必須唯一（專案未安裝 dbt_utils，以 singular test 檢查）
select 'quarter' as model_name, count(*) as duplicate_groups
from (
    select city_code, district, transaction_quarter
    from {{ ref('agg_district_quarter_price') }}
    group by city_code, district, transaction_quarter
    having count(*) > 1
)
having count(*) > 0
union all
select 'segment', count(*)
from (
    select city_code, district, building_type_group, age_band
    from {{ ref('agg_district_segment_price') }}
    group by city_code, district, building_type_group, age_band
    having count(*) > 1
)
having count(*) > 0
union all
select 'budget', count(*)
from (
    select city_code, district, budget_band
    from {{ ref('agg_district_budget') }}
    group by city_code, district, budget_band
    having count(*) > 1
)
having count(*) > 0
union all
select 'city_month', count(*)
from (
    select city_code, transaction_month
    from {{ ref('agg_city_month_market') }}
    group by city_code, transaction_month
    having count(*) > 1
)
having count(*) > 0
