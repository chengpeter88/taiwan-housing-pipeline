# 儀表板（Evidence ＋ GitHub Pages）

網址：https://chengpeter88.github.io/taiwan-housing-pipeline/

`dashboard/` 是 Evidence 專案：`sources/housing/*.sql` 在建置時查 BigQuery 的 `housing_gold`，`pages/*.md` 用 SQL（瀏覽器內的 DuckDB）加元件組成頁面。`.github/workflows/dashboard.yml` 建置成靜態網站後發布到 GitHub Pages。

| 頁面 | 來源表 | 內容 |
| --- | --- | --- |
| 首頁 | `build_metadata`、`agg_city_month_market` | 季別、分析期間、建置時間、住宅交易筆數、最新滾動中位數、口徑與授權 |
| 市場冷熱 | `agg_city_month_market` | 每月成交量、滾動 3 個月中位單價、成交量年增率 |
| 行政區趨勢 | `agg_district_quarter_price` | 選行政區看每季 P25／中位數／P75 與年增率；各區最新一季 |
| 型態 × 屋齡 | `agg_district_segment_price` | 大樓、華廈、公寓在各區、各屋齡帶的中位單價（熱圖與表） |
| 預算 | `agg_district_budget` | 選總價帶，看各區中位坪數、屋齡、房數、電梯與車位比例 |

## 設計

| 項目 | 做法 | 原因 |
| --- | --- | --- |
| 工具版本 | Evidence 40.1.8（npm 最後一版，2026-02），依賴由官方 template 同日的 lockfile 起算並鎖定 | 之後 Evidence 改走 Evidence Studio，npm 版不再更新；鎖定版本避免生態漂移（例如 TypeScript 7 與 svelte2tsx 衝突） |
| 憑證 | GitHub OIDC → Workload Identity Federation → `dashboard-reader`；Evidence 的 BigQuery 連線用 `authenticator: gcloud-cli`，交給 ADC 讀 auth action 產生的短期憑證檔 | repo 與 workflow 都沒有金鑰 |
| 誰能換到憑證 | provider 多對應 `attribute.repo_ref = repository_id:ref`，`dashboard-reader` 只綁 `<repository_id>:refs/heads/main` | 公開 repo：只有 main 上的 workflow 能讀正式 gold；dbt CI 仍綁整個 repository_id，不受影響 |
| 帳號權限 | 專案層 BigQuery Job User；`housing_gold` READER；其他 dataset 沒有權限 | 只讀、不能寫，也看不到 bronze／silver |
| 觸發 | push 到 main（`dashboard/**` 或此 workflow）、每天 06:30 UTC（台北 14:30，Airflow 12:00 之後）、手動 | 不在 pull_request 觸發：任何人都能對公開 repo 開 PR |
| 公開範圍 | source 只查 4 張彙總表與 `build_metadata`，明確列欄位；不查 `fct_housing_deal` | Evidence 會把 source 結果打包成 parquet 放進網站，等於公開下載 |
| 失敗不壞站 | `npm run sources:strict`、`npm run build:strict`，另外檢查 log 不得出現 `Error in Query` | 實測 40.1.8 的 `build:strict` 在頁面查詢錯誤時仍 exit 0；build 失敗時 deploy 不執行，Pages 保留上一版 |
| 統計口徑 | 頁面不對中位數／分位數做平均或加總，不用 `totalRow`；樣本少於 30 筆標示「樣本少」，熱圖不上色 | 與 Gold 層相同規則（pytest 檢查） |

pytest（`tests/test_dashboard_config.py`）守住以上設定：觸發條件、各 job 權限、WIF 變數、npm ci 與 strict、basePath 與輸出路徑、連線無金鑰欄位、套件版本精確、source 只讀允許的 gold 表且欄位存在於模型、頁面不聚合中位數、低樣本標示、首頁顯示新鮮度與授權、setup 腳本的 dashboard 授權。

## 一次性設定

1. **GCP**：在 Cloud Shell 重新執行 `bash setup_wif.sh <repository_id>`（見 [ci_dbt.md](ci_dbt.md)）。腳本會更新 provider 的 attribute mapping、建立 `dashboard-reader`，並在最後列出兩個帳號的全部權限；`dashboard-reader` 應該只有 `project roles/bigquery.jobUser` 與 `housing_gold: READER`。
2. **GitHub repo 變數**：Settings → Secrets and variables → Actions → **Variables**，新增 `GCP_DASHBOARD_SERVICE_ACCOUNT`（腳本最後印出的值）。`GCP_WIF_PROVIDER` 與 dbt CI 共用，已經存在。
3. **Pages**：Settings → Pages → Build and deployment → Source 選 **GitHub Actions**。
4. Actions → Dashboard → Run workflow（main），成功後開啟上方網址。

## 失敗時怎麼判斷

| 現象 | 可能原因 | 處理 |
| --- | --- | --- |
| `Check repository variables` 失敗 | 沒有設定 `GCP_DASHBOARD_SERVICE_ACCOUNT` | 照一次性設定第 2 步 |
| auth 步驟 `Permission denied` | 在 main 以外的分支手動觸發，或 provider 還沒有 `repo_ref` mapping | 只從 main 觸發；重新執行設定腳本 |
| `sources:strict` 找不到表 | gold 表到期（sandbox 60 天）或 Airflow 很久沒跑 | 本機 `python -m scripts.verify_bigquery`，讓 Airflow 重跑 |
| `sources:strict` Access Denied | `dashboard-reader` 少了 `housing_gold` READER，或 dataset 被重建 | 重新執行設定腳本 |
| build 步驟出現 `Error in Query` | 頁面 SQL 錯誤或 mart 欄位改名 | 看 log 中的查詢名稱；本機 build 重現 |
| deploy 失敗 | Pages 來源不是 GitHub Actions，或 `github-pages` environment 限制了分支 | 照一次性設定第 3 步 |

公開 repo 連續 60 天沒有活動時，GitHub 會停用排程；gold 表也是 60 天到期。首頁顯示 `built_at`，資料停更時看得出來。

## 本機開發

本機需要 Node（≥ 18，CI 用 22.23.5）與 gcloud ADC 登入（見 [local_environment.md](local_environment.md)）。

```bash
cd dashboard
npm ci
npm run sources:strict          # 用本機 ADC 查 housing_gold
npm run dev                     # http://localhost:3000/taiwan-housing-pipeline/
# 與 CI 相同的建置；重建前先刪 .evidence/template，否則增量建置可能沿用舊的編譯結果
rm -rf .evidence/template build
EVIDENCE_BUILD_DIR=./build/taiwan-housing-pipeline SEND_ANONYMOUS_USAGE_STATS=no npm run build:strict
```

Windows PowerShell 改用 `$env:EVIDENCE_BUILD_DIR='./build/taiwan-housing-pipeline'` 設定環境變數。頁面 SQL 的註解裡不要寫 `${查詢名}`，Evidence 連註解也會展開。

## 成本

- 查詢：5 個 source 都是幾十到一百多列的彙總表，每次建置掃描量為 KB 級；connector 對每個查詢會送兩次 job，仍遠低於每月 1 TiB 免費額度。
- 儲存：只讀，不建表，不消耗 sandbox 的終身 10 GiB。
- GitHub Actions：公開 repo 免費；網站約 87 MB（大部分是瀏覽器端的 DuckDB），低於 Pages 1 GB 上限。parquet 檔名沒有 hash，Pages 快取約 10 分鐘，重新部署後最多 10 分鐘看到舊數字。
