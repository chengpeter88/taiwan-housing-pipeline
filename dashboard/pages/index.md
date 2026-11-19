---
title: 臺北房市：價格撐住、成交量縮
---

```sql meta
select
    seasons,
    split_part(seasons, ',', 1) || '–' || split_part(seasons, ',', -1)
        || '（' || len(string_split(seasons, ',')) || ' 季）' as season_range,
    min_transaction_date,
    as_of_date,
    built_at
from housing.build_metadata
```

```sql market_story
-- 價格：最新滾動 3 個月中位數 vs 第一個完整窗口、vs 去年同月（兩個中位數相比，不做平均）
-- 成交量：最近 3 個月筆數 vs 去年同期（筆數可以加總）
with m as (
    select *
    from housing.city_month_market
),
latest as (
    select *
    from m
    order by transaction_month desc
    limit 1
),
first_full as (
    select *
    from m
    where is_rolling_window_complete
    order by transaction_month
    limit 1
),
year_ago as (
    select m.*
    from m
    inner join latest on m.transaction_month = latest.transaction_month - interval 12 month
)
select
    latest.transaction_month as latest_month,
    latest.rolling_3m_median_price_per_ping / 10000 as latest_rolling_10k,
    first_full.transaction_month as first_month,
    first_full.rolling_3m_median_price_per_ping / 10000 as first_rolling_10k,
    latest.rolling_3m_median_price_per_ping / first_full.rolling_3m_median_price_per_ping - 1 as rolling_change,
    latest.rolling_3m_median_price_per_ping / year_ago.rolling_3m_median_price_per_ping - 1 as rolling_yoy,
    latest.rolling_3m_deal_count as latest_3m_deals,
    latest.rolling_3m_deal_count / year_ago.rolling_3m_deal_count - 1 as deals_3m_yoy
from latest
cross join first_full
cross join year_ago
```

```sql volume_story
-- 前 12 個月與最近 12 個月的平均月成交量；以及年減最深的月份
with m as (
    select *
    from housing.city_month_market
),
bounds as (
    select min(transaction_month) as first_month, max(transaction_month) as last_month
    from m
)
select
    avg(case when m.transaction_month < b.first_month + interval 12 month then m.deal_count end) as first_12m_avg,
    avg(case when m.transaction_month > b.last_month - interval 12 month then m.deal_count end) as last_12m_avg,
    avg(case when m.transaction_month > b.last_month - interval 12 month then m.deal_count end)
        / avg(case when m.transaction_month < b.first_month + interval 12 month then m.deal_count end) - 1 as volume_change,
    arg_min(m.transaction_month, m.deal_count_yoy_ratio) as worst_month,
    min(m.deal_count_yoy_ratio) as worst_yoy
from m
cross join bounds as b
```

```sql district_story
-- 最新一季各區中位數年增；任一邊樣本少時 gold 已把年增設為 NULL，不列入
with q as (
    select *
    from housing.district_quarter_price
),
latest as (
    select max(transaction_quarter) as transaction_quarter
    from q
),
cur as (
    select q.*
    from q
    inner join latest on q.transaction_quarter = latest.transaction_quarter
    where q.median_yoy_ratio is not null
        and not q.is_low_sample
)
select
    year(max(transaction_quarter)) || ' Q' || quarter(max(transaction_quarter)) as quarter_label,
    count(*) as districts,
    count(*) filter (where median_yoy_ratio > 0) as districts_up,
    count(*) filter (where median_yoy_ratio > 0) || ' / ' || count(*) as districts_up_label,
    arg_max(district, median_yoy_ratio) as top_district,
    max(median_yoy_ratio) as top_yoy,
    arg_min(district, median_yoy_ratio) as bottom_district,
    min(median_yoy_ratio) as bottom_yoy
from cur
```

```sql premium
-- 同一區：屋齡 10 年內的住宅大樓 vs 30 年以上的公寓（兩個中位數相比）
select
    tower.district,
    tower.median_price_per_ping / 10000 as new_tower_10k,
    apartment.median_price_per_ping / 10000 as old_apartment_10k,
    tower.median_price_per_ping / apartment.median_price_per_ping - 1 as premium
from housing.district_segment_price as tower
inner join housing.district_segment_price as apartment
    on tower.district = apartment.district
where tower.building_type_group = 'tower'
    and tower.age_band = '0-10'
    and apartment.building_type_group = 'apartment'
    and apartment.age_band = '30+'
    and not tower.is_low_sample
    and not apartment.is_low_sample
order by premium desc
```

```sql premium_story
select
    count(*) as districts,
    arg_max(district, premium) as top_district,
    max(premium) as top_premium,
    arg_min(district, premium) as bottom_district,
    min(premium) as bottom_premium
from ${premium}
```

```sql old_apartment_share
-- 成交筆數可以加總：30 年以上公寓占全部住宅成交的比例
select
    sum(case when building_type_group = 'apartment' and age_band = '30+' then deal_count else 0 end)
        / sum(deal_count) as share
from housing.district_segment_price
```

```sql budget_story
-- 近 12 個月、總價 1500–2000 萬（含車位）：各區中位坪數最大與最小；以及全市 2000 萬以下的成交占比
with band as (
    select *
    from housing.district_budget
    where budget_band = '1500-2000'
        and not is_low_sample
),
all_bands as (
    select
        sum(case when budget_band in ('<1000', '1000-1500', '1500-2000') then deal_count else 0 end)
            / sum(deal_count) as under_2000_share
    from housing.district_budget
)
select
    arg_max(band.district, band.median_housing_area_ping) as big_district,
    max(band.median_housing_area_ping) as big_area,
    arg_min(band.district, band.median_housing_area_ping) as small_district,
    min(band.median_housing_area_ping) as small_area,
    any_value(all_bands.under_2000_share) as under_2000_share
from band
cross join all_bands
```

```sql budget_chart
select
    district,
    median_housing_area_ping,
    is_low_sample
from housing.district_budget
where budget_band = '1500-2000'
    and not is_low_sample
order by median_housing_area_ping desc
```

```sql market
select
    transaction_month,
    deal_count,
    rolling_3m_median_price_per_ping / 10000 as rolling_3m_median_10k,
    is_rolling_window_complete
from housing.city_month_market
order by transaction_month
```

```sql market_rolling
select
    transaction_month,
    rolling_3m_median_10k
from ${market}
where is_rolling_window_complete
```

<Grid cols=4>
    <BigValue
        data={market_story}
        value=latest_rolling_10k
        title="房價：滾動 3 個月中位單價"
        fmt='#,##0.0" 萬/坪"'
        comparison=rolling_yoy
        comparisonFmt='pct1'
        comparisonTitle="vs 去年同期"
    />
    <BigValue
        data={market_story}
        value=latest_3m_deals
        title="成交量：最近 3 個月"
        fmt='#,##0" 筆"'
        comparison=deals_3m_yoy
        comparisonFmt='pct1'
        comparisonTitle="vs 去年同期"
    />
    <BigValue
        data={district_story}
        value=districts_up_label
        title="中位數比去年高的行政區"
        comparison=top_yoy
        comparisonFmt='pct1'
        comparisonTitle="最高漲幅"
    />
    <BigValue
        data={budget_story}
        value=under_2000_share
        title="總價 2000 萬以下的成交占比"
        fmt='pct0'
    />
</Grid>

<p class="text-sm text-gray-500">
資料截至 <Value data={meta} column=as_of_date fmt='yyyy-mm-dd' />（臺北市住宅買賣，<Value data={meta} column=season_range />）。
房價為成交中位數，不是品質調整後的房價指數。
</p>

## 重點發現

### 1. 房價沒有跟著成交量下修，仍在期間高點

全市滾動 3 個月中位單價從 <Value data={market_story} column=first_month fmt='yyyy-mm' /> 的<Value data={market_story} column=first_rolling_10k fmt='#,##0.0' /> 萬/坪，
到 <Value data={market_story} column=latest_month fmt='yyyy-mm' /> 的<Value data={market_story} column=latest_rolling_10k fmt='#,##0.0' /> 萬/坪，
變化 <Value data={market_story} column=rolling_change fmt='pct1' />；與去年同期相比 <Value data={market_story} column=rolling_yoy fmt='pct1' />。

<LineChart
    data={market_rolling}
    x=transaction_month
    y=rolling_3m_median_10k
    yFmt='#,##0.0'
    yAxisTitle="萬元/坪"
    yMin=60
    title="全市滾動 3 個月中位單價"
/>

### 2. 成交量明顯縮小

最近 12 個月平均每月成交 <Value data={volume_story} column=last_12m_avg fmt='#,##0' /> 筆，
前 12 個月是 <Value data={volume_story} column=first_12m_avg fmt='#,##0' /> 筆（<Value data={volume_story} column=volume_change fmt='pct0' />）。
年減最深的是 <Value data={volume_story} column=worst_month fmt='yyyy-mm' />（<Value data={volume_story} column=worst_yoy fmt='pct0' />）。
量縮的時間點與 2024 年 9 月央行第 7 波選擇性信用管制相近；這裡只呈現時間上的關聯，沒有證明因果。

<BarChart
    data={market}
    x=transaction_month
    y=deal_count
    type=grouped
    yAxisTitle="筆"
    title="每月住宅成交量"
/>

### 3. 多數行政區比去年貴，但漲幅差很多

最新一季（<Value data={district_story} column=quarter_label />）有 <Value data={district_story} column=districts_up_label /> 個行政區的中位單價高於去年同季。
漲最多的是<Value data={district_story} column=top_district />（<Value data={district_story} column=top_yoy fmt='pct1' />），
最弱的是<Value data={district_story} column=bottom_district />（<Value data={district_story} column=bottom_yoy fmt='pct1' />）。
單季中位數會受成交組合影響，詳見[行政區趨勢](/districts)。

### 4. 同一區裡，新大樓比老公寓貴很多

屋齡 10 年內的住宅大樓，比 30 年以上的公寓每坪貴 <Value data={premium_story} column=bottom_premium fmt='pct0' />（<Value data={premium_story} column=bottom_district />）到<Value data={premium_story} column=top_premium fmt='pct0' />（<Value data={premium_story} column=top_district />）。
30 年以上的公寓占全部住宅成交的 <Value data={old_apartment_share} column=share fmt='pct0' />，是市場上最常見的產品。

<BarChart
    data={premium}
    x=district
    y=premium
    type=grouped
    swapXY=true
    yFmt='pct0'
    title="新大樓（0–10 年）比老公寓（30 年以上）每坪貴多少"
/>

### 5. 同樣預算，選區決定能買多大

近 12 個月總價 1500–2000 萬的住宅，在<Value data={budget_story} column=big_district />的中位坪數是<Value data={budget_story} column=big_area fmt='#,##0.0' /> 坪，在<Value data={budget_story} column=small_district />只有<Value data={budget_story} column=small_area fmt='#,##0.0' /> 坪（不含車位）。

<BarChart
    data={budget_chart}
    x=district
    y=median_housing_area_ping
    type=grouped
    swapXY=true
    yFmt='#,##0.0'
    title="總價 1500–2000 萬：各區中位坪數"
/>

其他總價帶見[預算](/budget)，各區各屋齡的單價見[型態 × 屋齡](/segments)。

<Details title="資料範圍、口徑與授權">

- 資料：內政部實價登錄，臺北市買賣，季別 <Value data={meta} column=seasons />；交易日期
  <Value data={meta} column=min_transaction_date fmt='yyyy-mm-dd' /> ～ <Value data={meta} column=as_of_date fmt='yyyy-mm-dd' />。
  BigQuery 最近一次完整建置成功：<Value data={meta} column=built_at fmt='yyyy-mm-dd H:MM' />（UTC）。
- 只納入住宅交易：房地或房地加車位，排除特殊關係與預售屋註記等案件；完整規則見 repo 的 README。
- 單價 =（總價 − 車位價）÷（建物面積 − 車位面積），以萬元/坪表示。
- 數字都是成交中位數；中位數不能平均或加總，跨期、跨區的數字由逐筆交易重新計算，或只做兩個中位數的比較。
- 樣本少於 30 筆的格子不列入上述結論，在各頁標示「樣本少」。
- 資料提供者：內政部地政司（[不動產成交案件實際資訊](https://plvr.land.moi.gov.tw/DownloadOpenData)），依
  [政府資料開放授權條款第 1 版](https://data.gov.tw/license) 使用。本頁是清理與彙總後的衍生分析，不代表政府官方統計。
- 原始碼：[github.com/chengpeter88/taiwan-housing-pipeline](https://github.com/chengpeter88/taiwan-housing-pipeline)

</Details>
