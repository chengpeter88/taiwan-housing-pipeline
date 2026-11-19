-- 行政區 × 建物型態 × 屋齡帶（整個分析期間合併）
select
    district,
    building_type_group,
    age_band,
    deal_count,
    p25_price_per_ping,
    median_price_per_ping,
    p75_price_per_ping,
    median_total_price_incl_parking_10k,
    is_low_sample
from `taiwan-household.housing_gold.agg_district_segment_price`
