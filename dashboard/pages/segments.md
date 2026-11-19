---
title: 型態 × 屋齡
---

同一區、同一種建物型態、同一個屋齡帶的中位單價（合併整個分析期間，以保留樣本）。空白代表該組合沒有成交，不是 0。

```sql segment_cells
select
    district,
    case building_type_group
        when 'tower' then '住宅大樓'
        when 'mansion' then '華廈'
        when 'apartment' then '公寓'
        when 'townhouse' then '透天厝'
        else '其他'
    end as building_type,
    building_type_group,
    age_band,
    deal_count,
    p25_price_per_ping / 10000 as p25_10k,
    median_price_per_ping / 10000 as median_10k,
    p75_price_per_ping / 10000 as p75_10k,
    median_total_price_incl_parking_10k,
    is_low_sample,
    case when is_low_sample then '樣本少' else '' end as sample_note
from housing.district_segment_price
where building_type_group in ('tower', 'mansion', 'apartment')
    and age_band <> 'unknown'
```

```sql types
select distinct building_type, building_type_group
from ${segment_cells}
order by building_type_group desc
```

<Dropdown data={types} name=building_type value=building_type title="建物型態" defaultValue="住宅大樓" />

```sql heat
-- 樣本少的格子不上色，避免少數幾筆主導顏色
select
    district,
    age_band,
    median_10k
from ${segment_cells}
where building_type = '${inputs.building_type.value}'
    and not is_low_sample
order by district, age_band
```

<Heatmap
    data={heat}
    x=age_band
    y=district
    value=median_10k
    valueFmt='#,##0.0'
    title="{inputs.building_type.value}：各區 × 屋齡中位單價（萬元/坪，樣本少的格子不顯示）"
/>

```sql detail
select
    district,
    age_band,
    deal_count,
    p25_10k,
    median_10k,
    p75_10k,
    median_total_price_incl_parking_10k,
    sample_note
from ${segment_cells}
where building_type = '${inputs.building_type.value}'
order by district, age_band
```

<DataTable data={detail} rows=36 search=true>
    <Column id=district title="行政區" />
    <Column id=age_band title="屋齡（年）" />
    <Column id=deal_count title="筆數" fmt='#,##0' />
    <Column id=p25_10k title="P25" fmt='#,##0.0' />
    <Column id=median_10k title="中位單價" fmt='#,##0.0' />
    <Column id=p75_10k title="P75" fmt='#,##0.0' />
    <Column id=median_total_price_incl_parking_10k title="中位總價（萬元，含車位）" fmt='#,##0' />
    <Column id=sample_note title="樣本" />
</DataTable>

屋齡帶：0-10、10-30、30+ 年（左閉右開）。透天厝與其他型態樣本太少，未列入。
