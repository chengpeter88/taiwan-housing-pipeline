---
title: 預算
---

近 12 個月內，總價（含車位）落在某個區間的住宅交易，在各區通常是幾坪、幾年、幾房。

```sql bands
select distinct
    budget_band,
    case budget_band
        when '<1000' then 1
        when '1000-1500' then 2
        when '1500-2000' then 3
        when '2000-3000' then 4
        when '3000-5000' then 5
        else 6
    end as band_order
from housing.district_budget
order by band_order
```

<Dropdown data={bands} name=budget_band value=budget_band order=band_order title="總價帶（萬元）" defaultValue="1500-2000" />

```sql budget
select
    district,
    deal_count,
    share_of_district_deals,
    median_housing_area_ping,
    median_building_age_years,
    median_room_count,
    elevator_share,
    parking_included_share,
    median_price_per_ping / 10000 as median_10k,
    is_low_sample,
    case when is_low_sample then '樣本少' else '' end as sample_note
from housing.district_budget
where budget_band = '${inputs.budget_band.value}'
order by median_housing_area_ping desc
```

<BarChart
    data={budget}
    x=district
    y=median_housing_area_ping
    type=grouped
    swapXY=true
    yFmt='#,##0.0'
    yAxisTitle="坪（不含車位）"
    title="總價 {inputs.budget_band.value} 萬元：各區中位坪數"
/>

<DataTable data={budget} rows=12>
    <Column id=district title="行政區" />
    <Column id=deal_count title="筆數" fmt='#,##0' />
    <Column id=share_of_district_deals title="占該區交易" fmt='pct0' />
    <Column id=median_housing_area_ping title="中位坪數" fmt='#,##0.0' />
    <Column id=median_building_age_years title="中位屋齡（年）" fmt='#,##0' />
    <Column id=median_room_count title="中位房數" fmt='#,##0' />
    <Column id=elevator_share title="有電梯" fmt='pct0' />
    <Column id=parking_included_share title="含車位" fmt='pct0' />
    <Column id=median_10k title="中位單價（萬元/坪）" fmt='#,##0.0' />
    <Column id=sample_note title="樣本" />
</DataTable>

坪數不含車位。屋齡、房數只計算有登記的交易；電梯比例只計算有填寫的交易。
