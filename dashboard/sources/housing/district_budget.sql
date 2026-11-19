-- 行政區 × 總價帶（近 12 個月）：能買到幾坪、屋齡、房數
select
    district,
    budget_band,
    deal_count,
    share_of_district_deals,
    median_housing_area_ping,
    median_building_age_years,
    age_known_count,
    median_room_count,
    room_known_count,
    elevator_share,
    parking_included_share,
    median_price_per_ping,
    is_low_sample
from `taiwan-household.housing_gold.agg_district_budget`
