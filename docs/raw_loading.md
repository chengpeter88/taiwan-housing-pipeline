# BigQuery 原始資料載入

載入器：`scripts/load_bigquery.py`。欄位對應：`config/column_mapping.json`。季別清單與分析期間的唯一來源：`config/seasons.json`。

## 範圍

台北市買賣（`a_lvr_land_a.csv`），112S4–115S3 共 12 季，73,133 列。季別是內政部的「登記期間」季檔，不是交易季度：

| 季別 | 買賣登記期間 |
| --- | --- |
| S1 | 前一年 12/11 – 3/10 |
| S2 | 3/11 – 6/10 |
| S3 | 6/11 – 9/10 |
| S4 | 9/11 – 12/10 |

## 指令

```powershell
cd E:\coding\taiwan-housing-pipeline
# 下載（或使用快取）並驗證，不連 BigQuery
.venv\Scripts\python.exe -m scripts.load_bigquery --season 115S3 --validate-only
# 下載、驗證並載入
.venv\Scripts\python.exe -m scripts.load_bigquery --season 115S3
# 使用本機 ZIP，不下載
.venv\Scripts\python.exe -m scripts.load_bigquery --season 114S4 --zip C:\path\lvr_landcsv.zip
# 刪除單季表
.venv\Scripts\python.exe -m scripts.load_bigquery --season 115S3 --drop
```

正式流程由 Airflow DAG 逐季呼叫，見 [airflow.md](airflow.md)。

## 下載與快取

- 來源：`https://plvr.land.moi.gov.tw/DownloadSeason?season=<季>&type=zip&fileName=lvr_landcsv.zip`。
- 以串流下載，上限 100 MB；內容開頭必須是 ZIP 標記 `PK\x03\x04`。未發布的季別官方回 HTTP 200 加 HTML 頁面，會在這裡被擋下。
- ZIP 存於 `data/landing/<sha256>/source.zip`，`data/landing/seasons/<季>.json` 記錄季別對應的 sha256、網址與下載時間。
- 預設重用快取（會重新核對 sha256）；`--refresh` 強制重新下載，內容變動時寫入警告。

## 驗證

載入前檢查：必要成員、解壓上限 500 MB、CRC、編碼、雙表頭、全部 33 欄映射、資料非空，以及 `build_time.xml` 的**買賣**登記起訖日必須等於該季別（只比對「登記日期…之買賣案件」那段，不會誤抓租賃或預售屋的日期）。

確定性錯誤（格式、未發布、期間不符、欄位變動等）exit code 為 3，排程不重試；網路或 BigQuery 暫時性錯誤保持可重試。stdout 只輸出一份純 ASCII 的 JSON，給呼叫端解析。

## BigQuery 表

- 每季一表：`taiwan-household.housing_raw.lvr_season_a_<季>`，區域 US。以 load job 的 WRITE_TRUNCATE 整表覆寫，重跑不累加。
- 33 個來源欄位皆為 STRING，原始字串不清理。另加 `city_code`、`season`、`source_sha256`、`source_row_number`、`loaded_at`。
- 載入後核對表的列數等於來源列數，結果寫入 `data/landing/<sha256>/load_result.json`，含表的到期時間。

2026-11-17 驗證：12 張表列數與官方 ZIP 逐季一致，共 73,133 列；編號在季內與跨季皆唯一。

## 注意事項

- 專案使用 BigQuery sandbox，表在建立後 60 天自動刪除。重建方式：手動觸發 Airflow DAG 一次，或用上方指令逐季重載，再執行 `dbt build`。
- 舊的單季表 `lvr_sale_a_<sha256>` 已不再使用，會自動到期，沒有手動刪除。
- 載入器只處理台北買賣；新增縣市需調整 `city_code` 與檔名，屬於後續工作。
