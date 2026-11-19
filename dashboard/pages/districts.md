---
title: 行政區趨勢
---

各區每季住宅中位單價與四分位距（P25～P75）。年增率是和去年同一季比較；任一邊樣本少時不計算年增率。

```sql districts
select distinct district
from housing.district_quarter_price
order by district
```

<Dropdown data={districts} name=district value=district title="行政區" defaultValue="大安區" />

```sql district_trend
select
    transaction_quarter,
    year(transaction_quarter) || ' Q' || quarter(transaction_quarter) as quarter_label,
    -- 圖例直接顯示欄位名（P25／Median／P75）
    p25_price_per_ping / 10000 as p25,
    median_price_per_ping / 10000 as median,
    p75_price_per_ping / 10000 as p75,
    deal_count,
    median_yoy_ratio,
    is_low_sample,
    case when is_low_sample then '樣本少' else '' end as sample_note
from housing.district_quarter_price
where district = '${inputs.district.value}'
order by transaction_quarter
```

<LineChart
    data={district_trend}
    x=transaction_quarter
    y={['p25', 'median', 'p75']}
    yFmt='#,##0.0'
    yAxisTitle="萬元/坪"
    yMin=40
    title="{inputs.district.value}：每季 P25／中位數／P75"
/>

<DataTable data={district_trend}>
    <Column id=quarter_label title="季" />
    <Column id=deal_count title="筆數" fmt='#,##0' />
    <Column id=p25 title="P25" fmt='#,##0.0' />
    <Column id=median title="中位數" fmt='#,##0.0' />
    <Column id=p75 title="P75" fmt='#,##0.0' />
    <Column id=median_yoy_ratio title="中位數年增" fmt='pct1' contentType=delta />
    <Column id=sample_note title="樣本" />
</DataTable>

## 各區最新一季

```sql latest_quarter
with latest as (
    select max(transaction_quarter) as transaction_quarter
    from housing.district_quarter_price
)
select
    q.district,
    year(q.transaction_quarter) || ' Q' || quarter(q.transaction_quarter) as quarter_label,
    q.deal_count,
    q.median_price_per_ping / 10000 as median_10k,
    q.prev_year_median_price_per_ping / 10000 as prev_year_median_10k,
    q.median_yoy_ratio,
    q.is_low_sample,
    case when q.is_low_sample then '樣本少' else '' end as sample_note
from housing.district_quarter_price as q
inner join latest as l on q.transaction_quarter = l.transaction_quarter
order by q.median_price_per_ping desc
```

<DataTable data={latest_quarter} rows=12>
    <Column id=district title="行政區" />
    <Column id=quarter_label title="季" />
    <Column id=deal_count title="筆數" fmt='#,##0' />
    <Column id=median_10k title="中位數" fmt='#,##0.0' />
    <Column id=prev_year_median_10k title="去年同季" fmt='#,##0.0' />
    <Column id=median_yoy_ratio title="年增" fmt='pct1' contentType=delta />
    <Column id=sample_note title="樣本" />
</DataTable>

單位：萬元/坪。中位數上漲也可能來自成交組合變化（例如較新的房子成交比較多），請搭配[型態 × 屋齡](/segments)一起看。
