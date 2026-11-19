---
title: 臺北實價登錄：房價與市場
---

```sql meta
select
    seasons,
    min_transaction_date,
    as_of_date,
    built_at
from housing.build_metadata
```

```sql kpi
-- 成交筆數可以加總；中位數不行，所以只取最新一個月的滾動中位數
with latest as (
    select max(transaction_month) as transaction_month
    from housing.city_month_market
)
select
    (select sum(deal_count) from housing.city_month_market) as total_deals,
    m.transaction_month,
    m.deal_count,
    m.rolling_3m_median_price_per_ping / 10000 as rolling_3m_median_10k,
    m.deal_count_yoy_ratio
from housing.city_month_market as m
inner join latest as l on m.transaction_month = l.transaction_month
```

資料：臺北市買賣實價登錄，季別 <Value data={meta} column=seasons />，
交易日期 <Value data={meta} column=min_transaction_date fmt='yyyy-mm-dd' /> ～
<Value data={meta} column=as_of_date fmt='yyyy-mm-dd' />。
BigQuery 最近一次完整建置成功：<Value data={meta} column=built_at fmt='yyyy-mm-dd H:MM' />（UTC）。

<BigValue data={kpi} value=total_deals title="住宅交易筆數" fmt='#,##0' />
<BigValue data={kpi} value=rolling_3m_median_10k title="最新滾動 3 個月中位單價（萬元/坪）" fmt='#,##0.0' />
<BigValue data={kpi} value=deal_count title="最新月成交量" fmt='#,##0' comparison=deal_count_yoy_ratio comparisonFmt='pct1' comparisonTitle="年增" />

## 頁面

- [市場冷熱](/market)：全市每月成交量與滾動 3 個月中位單價
- [行政區趨勢](/districts)：各區每季中位數、P25／P75 與年增率
- [型態 × 屋齡](/segments)：大樓、華廈、公寓在不同屋齡的單價差距
- [預算](/budget)：某個總價帶在各區能買到幾坪、幾年的房子

## 口徑

- 只納入住宅交易：房地或房地加車位，排除特殊關係與預售屋註記等案件；完整規則見 repo 的 README。
- 單價 =（總價 − 車位價）÷（建物面積 − 車位面積），換算成萬元/坪。
- 顯示的是成交**中位數**，不是品質調整後的房價指數：某區中位數上漲，可能只是那段期間成交的房子比較新。
- 中位數不能平均或加總；跨期、跨區的數字都由逐筆交易重新計算。
- 樣本少於 30 筆的格子標示「樣本少」，解讀時要保守。

## 資料來源與授權

資料提供者：內政部地政司（[不動產成交案件實際資訊](https://plvr.land.moi.gov.tw/DownloadOpenData)），
依 [政府資料開放授權條款第 1 版](https://data.gov.tw/license) 使用。本頁是清理與彙總後的衍生分析，不代表政府官方統計。
原始碼：[github.com/chengpeter88/taiwan-housing-pipeline](https://github.com/chengpeter88/taiwan-housing-pipeline)。
