# BigQuery staging

模型：`taiwan-household.housing_dev.stg_lvr_sale`。來源為 `analytics_bigquery/dbt_project.yml` 的 `raw_snapshot`，明確指定單一檔案版本，不使用 wildcard 合併所有 raw 表。

```powershell
.venv\Scripts\dbt.exe build --project-dir analytics_bigquery --profiles-dir analytics_bigquery --select stg_lvr_sale
```

分析窗口由 `min_transaction_date` 與 `as_of_date` 控制，目前分別為 2025-06-01、2026-11-16。固定截止日讓結果可重現；更新批次時需明確更新，不能假設它自動代表今天。

所有來源欄位與原始字串保留。新增 transaction_date、NUMERIC 價格與面積、特殊交易旗標、住宅用途、日期有效性、車位狀態及分析資格。無效轉型使用 SAFE，結果為 NULL，不使整批 SQL 中斷。

staging 一列仍代表來源的一列，以 source_sha256 與 source_row_number 組成唯一鍵。沒有刪除任何交易。同城市編號若重複，標記 duplicate_serial_review 並暫停納入價格分析，沒有自行挑一筆覆蓋。

獨立旗標可重疊；exclusion_reason 按 SQL 固定順序歸類，每列只有一個主要原因，適合做排除筆數對帳。is_price_eligible 表示合格房地交易，is_residential_price_eligible 另外要求主要用途等於住家用。其他用途不從 staging 刪除。

車位無法拆算者標記 unclear_or_inconsistent，排除於主要價格分析；有拆價及面積仍不保證申報正確。特殊交易用完整已知用語比對，不涵蓋所有自由文字。此階段尚未建立價格 mart、跨期去重或正式展示發布閘門。
