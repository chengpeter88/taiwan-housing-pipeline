---
title: 市場冷熱
---

全市住宅交易的每月成交量與單價。滾動 3 個月中位數由三個月內的逐筆交易重新計算，不是三個月中位數的平均。

```sql market
select
    transaction_month,
    deal_count,
    median_price_per_ping / 10000 as median_10k,
    rolling_3m_median_price_per_ping / 10000 as rolling_3m_median_10k,
    rolling_3m_deal_count,
    is_rolling_window_complete,
    deal_count_yoy_ratio
from housing.city_month_market
order by transaction_month
```

```sql market_rolling
-- 前兩個月不足 3 個月，不畫滾動線
select *
from ${market}
where is_rolling_window_complete
```

<LineChart
    data={market_rolling}
    x=transaction_month
    y=rolling_3m_median_10k
    yFmt='#,##0.0'
    yAxisTitle="萬元/坪"
    yMin=60
    title="滾動 3 個月中位單價"
/>

<BarChart
    data={market}
    x=transaction_month
    y=deal_count
    type=grouped
    yAxisTitle="筆"
    title="每月成交量"
/>

<BarChart
    data={market}
    x=transaction_month
    y=deal_count_yoy_ratio
    yFmt='pct0'
    title="成交量年增率（與去年同月相比；前 12 個月沒有比較基準）"
/>

<DataTable data={market} rows=36>
    <Column id=transaction_month title="月份" fmt='yyyy-mm' />
    <Column id=deal_count title="成交量" fmt='#,##0' />
    <Column id=deal_count_yoy_ratio title="成交量年增" fmt='pct1' contentType=delta />
    <Column id=median_10k title="月中位單價" fmt='#,##0.0' />
    <Column id=rolling_3m_median_10k title="滾動 3 個月中位單價" fmt='#,##0.0' />
    <Column id=rolling_3m_deal_count title="滾動 3 個月筆數" fmt='#,##0' />
</DataTable>

單位：萬元/坪。成交量下降的時間點與 2024 年 9 月央行第 7 波選擇性信用管制相近，但這裡只呈現時間上的關聯，沒有證明因果。
