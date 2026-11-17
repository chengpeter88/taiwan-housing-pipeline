# BigQuery 原始資料載入

本階段使用本機官方 ZIP，尚未自動下載來源。載入器是 `scripts/load_bigquery.py`，欄位對應為 `config/column_mapping.json`，使用 pandas 與 google-cloud-bigquery，沒有新增套件。

```powershell
cd E:\coding\taiwan-housing-pipeline
.venv\Scripts\python.exe -m scripts.load_bigquery --zip C:\Users\User\Downloads\lvr_landcsv.zip --validate-only
.venv\Scripts\python.exe -m scripts.load_bigquery --zip C:\Users\User\Downloads\lvr_landcsv.zip
```

`--validate-only` 只讀取並保存本機來源及 metadata，不連線 BigQuery。載入前檢查 ZIP CRC、必要檔案、解壓大小、編碼、資料非空、雙表頭與全部 33 欄映射。新增或減少欄位時停止，先由人檢查欄位對應。

預設目標為 `taiwan-household.housing_raw`，區域 `US`。表名為 `lvr_sale_a_<完整 SHA256>`。每個來源檔案版本各自保存；同檔重跑以 load job 的 WRITE_TRUNCATE 覆寫同表，不追加交易。原始 ZIP 保存在 `data/landing/<SHA256>/source.zip`，metadata 同目錄保存，載入結果寫入 `data/bigquery_load_result.json`。

33 個來源欄位皆為 STRING，空白仍為空字串，民國日期及零值不轉換、不清理、不去重。另有 `city_code`、`source_sha256`、`source_row_number`、`loaded_at`。交易列以來源雜湊與列序追蹤；不能假設 raw 的編號永遠唯一。

重跑後業務資料不變，但 loaded_at 與 load job ID 會更新。該方式是固定來源重放，尚不是跨期最新資料管理；不同雜湊表不能直接 UNION 後宣稱沒有重複。後續 dbt 必須明確選用來源快照並處理交易衝突。來源收錄期間不等於官方發布日期。

此階段只驗證來源與 BigQuery 列數一致，不代表房價指標或清理口徑已完成。staging、車位旗標、住宅用途篩選與展示發布仍待實作。沙箱資料表有到期限制，原檔需保留以供重建。
