-- gold 彙總表與逐筆 fact 對帳；只比 deal_count（滾動筆數刻意重疊，不參與對帳）
with residential as (
    select * from {{ ref('fct_housing_deal') }} where is_residential
),
budget_window as (
    select count(*) as n
    from residential
    where transaction_month >= date_sub(
        date_trunc(date('{{ var("as_of_date") }}'), month),
        interval {{ var('budget_months') - 1 }} month)
),
checks as (
    select 'quarter_vs_fact' as check_name,
        (select sum(deal_count) from {{ ref('agg_district_quarter_price') }}) as actual,
        (select count(*) from residential) as expected
    union all
    select 'segment_vs_fact',
        (select sum(deal_count) from {{ ref('agg_district_segment_price') }}),
        (select count(*) from residential)
    union all
    select 'city_month_vs_fact',
        (select sum(deal_count) from {{ ref('agg_city_month_market') }}),
        (select count(*) from residential)
    union all
    select 'budget_vs_fact_window',
        (select sum(deal_count) from {{ ref('agg_district_budget') }}),
        (select n from budget_window)
)
select check_name, actual, expected
from checks
where actual is distinct from expected
union all
-- 季表每格 = 月表同區三個月加總
select 'quarter_vs_month', q.deal_count, m.deal_count
from {{ ref('agg_district_quarter_price') }} as q
left join (
    select city_code, district, date_trunc(transaction_month, quarter) as transaction_quarter,
        sum(deal_count) as deal_count
    from {{ ref('agg_district_month_price') }}
    group by city_code, district, transaction_quarter
) as m using (city_code, district, transaction_quarter)
where q.deal_count is distinct from m.deal_count
union all
-- 全市月表每月 = 各區月表加總
select 'city_month_vs_districts', c.deal_count, d.deal_count
from {{ ref('agg_city_month_market') }} as c
left join (
    select city_code, transaction_month, sum(deal_count) as deal_count
    from {{ ref('agg_district_month_price') }}
    group by city_code, transaction_month
) as d using (city_code, transaction_month)
where c.deal_count is distinct from d.deal_count
