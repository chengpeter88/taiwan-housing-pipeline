# CI 執行 dbt（Workload Identity Federation）

`.github/workflows/dbt-ci.yml` 在 PR 改到 `analytics_bigquery/`、`config/seasons.json`、`requirements.lock` 或此 workflow 時，以及在 Actions 頁手動觸發時，執行 `dbt build --target ci`。全部 8 個模型、11 組 unit tests 與資料測試都在真實 BigQuery 上跑，但只寫入 `ci_housing_silver`、`ci_housing_gold`，不碰正式資料。

## 設計

| 項目 | 做法 | 原因 |
| --- | --- | --- |
| 憑證 | GitHub OIDC → Workload Identity Federation → 模擬 `dbt-ci` service account，token 1 小時 | repo 不存任何金鑰 |
| 誰能換到憑證 | provider 的 attribute condition 只接受本 repo 的 `repository_id` | 用不可重用的數字 ID，不用名稱比對，避免 repo 改名或搶註 |
| CI 帳號權限 | 專案層只有 BigQuery Job User；`housing_bronze` READER；`ci_housing_*` WRITER；正式 silver／gold 沒有任何權限 | 即使 dbt 設定出錯，也寫不進正式資料 |
| 寫入什麼 | `ci` target 的模型全部是 view（`dbt_project.yml` 依 target 決定） | sandbox 儲存是**終身 10 GiB、刪除不退**，view 不佔儲存 |
| 同時多個 PR | workflow 的 concurrency 群組 `dbt-ci` 排隊（`queue: max`），不中途取消 | 同一時間只有一個 run 寫 `ci_housing_*` |
| 最後一步 | `python -m scripts.check_ci_permissions` 嘗試在 housing_silver／housing_gold 建表，必須被拒絕 | 每次都實際證明權限沒有被放大 |

dataset 名稱由 [generate_schema_name](../analytics_bigquery/macros/generate_schema_name.sql) 決定：`ci` target 寫 `ci_housing_<層>`。`ci_housing_*` 由設定腳本預先建立，CI 帳號沒有建立 dataset 的權限；如果被刪除，dbt 會因權限不足而失敗，重新執行設定腳本即可。

## 一次性設定

1. **取得 repository_id**：到 GitHub 的 Actions 頁，選「dbt CI」→ Run workflow。repo 變數還沒設定時，「Check repository variables」步驟會失敗並印出 `repository_id=<數字>`。
2. **GCP 設定**：在 [Cloud Shell](https://shell.cloud.google.com) 上傳或貼上 [infra/setup_wif.sh](../infra/setup_wif.sh)，執行：

   ```bash
   bash setup_wif.sh <repository_id>
   ```

   腳本可重複執行；最後印出 CI 帳號擁有的全部權限，應該只有：`project: roles/bigquery.jobUser`、`housing_bronze: READER`、`ci_housing_silver: WRITER`、`ci_housing_gold: WRITER`。
3. **GitHub repo 變數**：Settings → Secrets and variables → Actions → **Variables**，新增腳本最後印出的 `GCP_WIF_PROVIDER` 與 `GCP_CI_SERVICE_ACCOUNT`。這兩個值不是機密，所以放 Variables 而不是 Secrets。
4. 回到 Actions 再跑一次「dbt CI」，應該全部通過。

## CI 失敗時怎麼判斷

CI 讀的是正式的 `housing_bronze`，所以失敗不一定是 PR 的 SQL 有問題：

| 現象 | 可能原因 | 處理 |
| --- | --- | --- |
| `season_completeness`、`staging_row_reconciliation` 失敗，或找不到季表 | bronze 表到期或缺季 | 本機執行 `python -m scripts.verify_bigquery` 確認，再讓 Airflow 重跑 |
| PR 新增了季別就失敗 | 新季還沒載入 bronze | 先讓 Airflow 載入新季 |
| 所有 PR 同時壞掉、錯誤指向 wildcard schema | bronze 出現欄位不相容的新季表 | 檢查載入器與 `column_mapping.json` |
| `Check repository variables` 失敗 | repo 變數未設定 | 照上方一次性設定 |
| auth 步驟 `Permission denied` | provider 條件或 workloadIdentityUser 綁定不符 | 用正確的 repository_id 重新執行設定腳本 |
| `check_ci_permissions` 失敗 | CI 帳號被多給了正式 dataset 的權限 | 移除多餘權限 |

## 驗收紀錄（2026-11-19）

| 項目 | 結果 |
| --- | --- |
| 變數未設定 | 「Check repository variables」失敗並印出 repository_id，後續步驟未執行 |
| GCP 設定腳本 | 權限稽核只有 `project: roles/bigquery.jobUser`、`housing_bronze: READER`、`ci_housing_silver: WRITER`、`ci_housing_gold: WRITER` |
| 手動觸發 dbt CI | 成功：WIF 驗證、dbt build、正式 dataset 寫入探測皆被拒 |
| BigQuery 查核 | `ci_housing_silver` 1 個、`ci_housing_gold` 7 個物件全部是 VIEW，儲存 0 bytes；`ci_housing_gold.fct_housing_deal` 30,756 列，與正式表相同；正式 silver／gold 的修改時間未變，沒有殘留探測表；`verify_bigquery` 通過 |

注意：設定的是 **Repository variables**，不是 Secrets，也不是 Environment variables，否則 workflow 讀不到。

## 成本

- 查詢：每次 build 掃描量為 MB 級，sandbox 每月 1 TiB 查詢免費。
- 儲存：只有 view，幾乎為零。
