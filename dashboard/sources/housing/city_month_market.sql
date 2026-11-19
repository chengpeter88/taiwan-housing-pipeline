-- 全市每月：成交量、月中位數、滾動 3 個月中位數（由逐筆重算）、成交量年增率
select
    transaction_month,
    deal_count,
    median_price_per_ping,
    rolling_3m_median_price_per_ping,
    rolling_3m_deal_count,
    is_rolling_window_complete,
    deal_count_yoy_ratio
from `taiwan-household.housing_gold.agg_city_month_market`
