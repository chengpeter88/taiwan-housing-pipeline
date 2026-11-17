-- 區 x 建物型態 x 屋齡帶；時間合併整個分析期間以保留樣本數。空格子不會出現（不補 0）。
select distinct city_code, district, building_type_group, age_band,
    count(*) over segment as deal_count,
    percentile_cont(price_per_ping, 0.25) over segment as p25_price_per_ping,
    percentile_cont(price_per_ping, 0.5) over segment as median_price_per_ping,
    percentile_cont(price_per_ping, 0.75) over segment as p75_price_per_ping,
    percentile_cont(total_price_incl_parking_10k, 0.5) over segment as median_total_price_incl_parking_10k,
    count(*) over segment < {{ var('low_sample_threshold') }} as is_low_sample
from {{ ref('fct_housing_deal') }}
where is_residential
window segment as (partition by city_code, district, building_type_group, age_band)
