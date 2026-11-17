-- 季別精確分位數（逐筆算，不是月中位數的平均）；年增率以日期 join 去年同季，不用 LAG（缺季不會錯位）。
with quarterly as (
    select distinct city_code, district, transaction_quarter,
        count(*) over district_quarter as deal_count,
        percentile_cont(price_per_ping, 0.25) over district_quarter as p25_price_per_ping,
        percentile_cont(price_per_ping, 0.5) over district_quarter as median_price_per_ping,
        percentile_cont(price_per_ping, 0.75) over district_quarter as p75_price_per_ping
    from {{ ref('fct_housing_deal') }}
    where is_residential
    window district_quarter as (partition by city_code, district, transaction_quarter)
), flagged as (
    select *, deal_count < {{ var('low_sample_threshold') }} as is_low_sample
    from quarterly
)
select cur.city_code, cur.district, cur.transaction_quarter, cur.deal_count,
    cur.p25_price_per_ping, cur.median_price_per_ping, cur.p75_price_per_ping,
    prev.median_price_per_ping as prev_year_median_price_per_ping,
    -- 前期不存在、或任一期樣本不足時不給年增率
    case
        when prev.median_price_per_ping is null or cur.is_low_sample or prev.is_low_sample then null
        -- percentile_cont 輸出為 FLOAT64；比率統一四捨五入到小數 6 位，避免 0.19999999999999996
        else round(safe_divide(cur.median_price_per_ping, prev.median_price_per_ping) - 1, 6)
    end as median_yoy_ratio,
    cur.is_low_sample
from flagged as cur
left join flagged as prev
    on prev.city_code = cur.city_code
    and prev.district = cur.district
    and prev.transaction_quarter = date_sub(cur.transaction_quarter, interval 1 year)
