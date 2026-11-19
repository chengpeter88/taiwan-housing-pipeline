# 品質與 CI

## 驗證結果（2026-11-19）

- **pytest 150 項通過**：季別解析、官方下載（含未發布季別回 HTML、超過上限）、快取、12 季真實 `build_time.xml` 的登記期間、ZIP 與欄位驗證、載入與列數核對、dataset 自動建立、CLI exit code、設定與分層一致性、每日排程的新季別偵測、截止日規則與執行判斷、BigQuery 實況驗證、CI 權限與 workflow 設定、儀表板的公開範圍、權限與統計口徑。
- **`dbt build` 84 項通過（8 個模型）**：11 組 unit tests 以手算的小資料覆蓋 12 種排除原因、6 位數日期、屋齡週年邊界、分組邊界、精確分位數、年增率、滾動中位數與 NULL 處理；其餘資料測試涵蓋唯一鍵、允許值、季別完整性、各表與 fact 及月表的交叉對帳、價格公式與分位數順序。
- **獨立重算**：BigQuery 12 張季表的列數與官方 ZIP 逐季一致；pandas 從 Silver 獨立重算 fact 衍生欄位與全部 Gold 彙總表，每一欄都完全一致。
- **Airflow 3.4.0 容器**：12 季全量執行成功、同參數重跑筆數不變、未發布與格式錯誤的季別在對應任務失敗且不重試（見 [airflow.md](airflow.md)）。
- **BigQuery 實況**：`python -m scripts.verify_bigquery` 檢查三層表是否齊全、筆數／大小／剩餘天數、bronze 與官方檔逐季對帳、`build_metadata` 與 config 是否一致、每次全量的儲存用量；有問題時 exit code 1，`--json` 輸出 JSON。

## GitHub Actions

| Workflow | 觸發 | 做什麼 | 憑證 |
| --- | --- | --- | --- |
| `ci.yml` | push 到 main、每個 PR | `ruff check`、`black --check`、pytest；不下載官方檔 | 不需要 |
| `dbt-ci.yml` | 改到 dbt 相關檔案的 PR、手動 | 對真實 BigQuery 執行 `dbt build --target ci`，模型一律建 view、寫入 `ci_housing_*`；最後實際嘗試寫入正式 dataset 並確認被拒 | WIF → `dbt-ci`（見 [ci_dbt.md](ci_dbt.md)） |
| `dashboard.yml` | main 的儀表板變更、每天台北 14:30、手動；PR 不觸發 | 查 `housing_gold`、建置 Evidence 網站、發布 GitHub Pages；任何查詢錯誤都讓建置失敗，網站保留上一版 | WIF → `dashboard-reader`，只限 main（見 [dashboard.md](dashboard.md)） |

格式與 lint 設定在 `pyproject.toml`（行寬 100、保留單引號）。dbt 的 dataset 名稱由 [generate_schema_name](../analytics_bigquery/macros/generate_schema_name.sql) 決定：target 為 `prod` 時寫入 `housing_silver`、`housing_gold`；其他 target 自動加前綴（例如 `ci_housing_gold`），不會覆蓋正式資料。
