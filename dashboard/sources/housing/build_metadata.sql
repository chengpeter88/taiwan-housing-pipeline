-- 最近一次完整建置成功的季別與分析期間
select
    seasons,
    min_transaction_date,
    as_of_date,
    built_at
from `taiwan-household.housing_gold.build_metadata`
