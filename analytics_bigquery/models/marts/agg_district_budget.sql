-- 預算買得到什麼：最近 budget_months 個月（含 as_of 當月）、依含車位總價分帶。
with windowed as (
    select *
    from {{ ref('fct_housing_deal') }}
    where is_residential
        and transaction_month >= date_sub(
            date_trunc(date('{{ var("as_of_date") }}'), month),
            interval {{ var('budget_months') - 1 }} month)
        and transaction_month <= date_trunc(date('{{ var("as_of_date") }}'), month)
)
select distinct city_code, district, budget_band,
    count(*) over district_band as deal_count,
    -- 同區同視窗的占比（比率：0.2 = 20%）
    round(safe_divide(count(*) over district_band, count(*) over district_all), 6) as share_of_district_deals,
    percentile_cont(housing_area_ping, 0.5) over district_band as median_housing_area_ping,
    -- 屋齡未知（NULL）不參與中位數，另列已知筆數
    percentile_cont(building_age_years, 0.5) over district_band as median_building_age_years,
    count(building_age_years) over district_band as age_known_count,
    -- 0 房視為未申報，不參與房數中位數
    percentile_disc(if(room_count > 0, room_count, null), 0.5) over district_band as median_room_count,
    countif(room_count > 0) over district_band as room_known_count,
    -- 分母只算有申報電梯的筆數
    round(safe_divide(countif(has_elevator) over district_band, count(has_elevator) over district_band), 6) as elevator_share,
    round(safe_divide(countif(is_parking_included) over district_band, count(*) over district_band), 6) as parking_included_share,
    percentile_cont(price_per_ping, 0.5) over district_band as median_price_per_ping,
    count(*) over district_band < {{ var('low_sample_threshold') }} as is_low_sample
from windowed
window
    district_band as (partition by city_code, district, budget_band),
    district_all as (partition by city_code, district)
