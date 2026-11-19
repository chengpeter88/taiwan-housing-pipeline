-- 行政區 × 季：P25／中位數／P75 與年增率
select
    district,
    transaction_quarter,
    deal_count,
    p25_price_per_ping,
    median_price_per_ping,
    p75_price_per_ping,
    prev_year_median_price_per_ping,
    median_yoy_ratio,
    is_low_sample
from `taiwan-household.housing_gold.agg_district_quarter_price`
