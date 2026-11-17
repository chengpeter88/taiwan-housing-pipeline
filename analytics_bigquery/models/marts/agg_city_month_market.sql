-- 全市月度市場：當月精確中位數 + 滾動 3 個月中位數（由逐筆交易重算，不是月中位數平均）。
with residential as (
    select city_code, transaction_month, price_per_ping
    from {{ ref('fct_housing_deal') }}
    where is_residential
), monthly as (
    -- 先分開算當月筆數與中位數，避免被滾動 join 放大
    select distinct city_code, transaction_month,
        count(*) over city_month as deal_count,
        percentile_cont(price_per_ping, 0.5) over city_month as median_price_per_ping
    from residential
    window city_month as (partition by city_code, transaction_month)
), rolling as (
    -- anchor 月 join 其前 2 個月至本月的逐筆交易
    select distinct anchor.city_code, anchor.transaction_month,
        count(*) over anchor_window as rolling_3m_deal_count,
        percentile_cont(deal.price_per_ping, 0.5) over anchor_window as rolling_3m_median_price_per_ping
    from monthly as anchor
    inner join residential as deal
        on deal.city_code = anchor.city_code
        and deal.transaction_month between date_sub(anchor.transaction_month, interval 2 month)
            and anchor.transaction_month
    window anchor_window as (partition by anchor.city_code, anchor.transaction_month)
)
select cur.city_code, cur.transaction_month, cur.deal_count, cur.median_price_per_ping,
    rolling.rolling_3m_median_price_per_ping, rolling.rolling_3m_deal_count,
    -- 窗口前兩個月不足 3 個月
    cur.transaction_month >= date_add(
        date_trunc(date('{{ var("min_transaction_date") }}'), month), interval 2 month
    ) as is_rolling_window_complete,
    round(safe_divide(cur.deal_count, prev.deal_count) - 1, 6) as deal_count_yoy_ratio
from monthly as cur
inner join rolling
    on rolling.city_code = cur.city_code
    and rolling.transaction_month = cur.transaction_month
left join monthly as prev
    on prev.city_code = cur.city_code
    and prev.transaction_month = date_sub(cur.transaction_month, interval 1 year)
