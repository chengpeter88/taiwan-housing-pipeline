# Airflow DAG

狀態：2026-11-18 起每天自動執行，已在 Docker（Airflow 3.4.0、Python 3.12）實測。

DAG：`orchestration/dags/housing_pipeline.py`，dag_id `taipei_housing_pipeline`（原 `taipei_housing_manual`，改為排程後更名；舊名稱的執行紀錄仍留在 Airflow，但已不再使用）。使用 TaskFlow API，不需要 Google provider；判斷、載入與轉換都呼叫專案既有的 Python 模組與 dbt，不複製邏輯。

## 什麼時候會跑

每天台北時間 12:00 執行一次 `plan`。它只花幾秒，判斷「需不需要」全量重跑；不需要時下游全部 skipped。

需要重跑的條件（任一成立）：

| 原因 | 意思 |
| --- | --- |
| `no_successful_build` | `housing_gold.build_metadata` 不存在，代表從來沒有成功建置過，或表已到期消失 |
| `state_changed` | 期望的季別或分析期間與上次成功建置的不同，例如官方發布了新季別 |
| `missing_tables` | bronze、silver、gold 任一層缺表 |
| `expiring_tables` | 任一層的表在 `refresh_before_expiry_days`（預設 14）天內到期 |

選擇中午而不是凌晨，是因為 Airflow 跑在本機 Docker，電腦關機時不會執行。季檔一季才更新一次，晚幾天跑沒有影響；compose 已設定 `restart: unless-stopped`，Docker Desktop 啟動後容器會自動起來。

## 全自動更新規則

- **新季別**：以 `config/seasons.json` 的季別為基準，往後逐季呼叫載入器的下載函式探測。官方回傳 ZIP 就納入，回傳 HTML（未發布）就停止。已發布的新季檔會同時存進下載快取。一次發現超過 1 個新季時會記警告。
- **分析截止日**：最後一季的登記截止日往前 160 天（`as_of_lag_days`），取當天或之前最近的月底。依此規則，實際落差在 160～190 天之間；115S3 得 2026-03-31，115S4 得 2026-06-30。起日固定在 2023-10-01，分析期間會隨新季別拉長，這是刻意的設計。
- **狀態紀錄**：dbt 最後才建 `housing_gold.build_metadata`（季別、起日、截止日、建置時間）。它依賴所有 gold 彙總表，任何上游測試失敗就會被跳過，所以這張表的內容一定是「完整建置成功」的狀態。Looker Studio 也可以讀它，顯示資料實際的截止日。
- **到期延長**：BigQuery sandbox 的表到期日在建表時決定，重新載入（WRITE_TRUNCATE）不會延長。載入器因此改為先刪表再載入；dbt 每次也都重建表，所以每次全量執行後三層的到期日都會往後推 60 天。

## 執行流程

`plan → gate → season_list → fetch_validate（每季一個）→ load_season（每季一個）→ build_models`

- `plan`：執行 `python -m scripts.seasons plan`，輸出季別、截止日、缺表、快到期的表、原因與 `dbt_vars`。
- `gate`：`should_run` 為真或手動 `force` 時放行，並在 log 寫出原因；否則下游全部 skipped。
- `season_list`：取出季別清單給 dynamic task mapping（Airflow 不能直接對 dict 的鍵展開）。
- `fetch_validate`：下載（或使用快取）並驗證該季，XCom 只傳季別、sha256 與筆數。
- `load_season`：用驗證過的同一份 `data/landing/<sha256>/source.zip` 載入 BigQuery（先刪表再載入）。
- `build_models`：全部季別載入後，以 `plan` 的 `dbt_vars` 執行 `dbt build`。

## 參數（手動觸發時可調整）

| 參數 | 預設 | 說明 |
| --- | --- | --- |
| force | false | 不管判斷結果，強制全量執行 |
| refresh | false | 忽略快取，重新下載官方檔 |
| refresh_before_expiry_days | 14 | 距到期幾天內就重跑；設大一點可以提前延長到期日 |

DAG 原始碼不寫死任何季別或日期（pytest 檢查）。

## 本機手動執行 dbt

自動更新後，BigQuery 裡的季別與截止日可能比 repo 的 `dbt_project.yml` 新。手動執行 `dbt build` 時要帶上目前的期望狀態，否則會把 gold 退回 repo 裡的舊設定：

```powershell
.venv\Scripts\python.exe -m scripts.seasons plan
# 把輸出的 dbt_vars 帶給 dbt
.venv\Scripts\dbt.exe build --project-dir analytics_bigquery --profiles-dir analytics_bigquery --vars '<dbt_vars 的 JSON>'
```

新季別納入後，建議同步更新 `config/seasons.json` 與 `dbt_project.yml` 並提交，讓 repo 與 BigQuery 一致。

## 重試策略

- 確定性錯誤（載入器 exit code 2 或 3：季別格式錯、未發布、登記期間不符、欄位變動）→ `AirflowFailException`，不重試。
- 網路或 BigQuery 暫時性錯誤 → 最多重試 2 次。
- `build_models` 不重試：dbt 測試失敗是資料或邏輯問題，重跑只會多耗查詢額度。
- 每次最多一個 DAG run、一個 task；整體逾時 90 分鐘。

## 啟動

在專案根目錄執行。第一次先複製並編輯 `.env`（已存在就保留，不要覆蓋）：

```powershell
Copy-Item orchestration/.env.example orchestration/.env
# 編輯 GCP_ADC_PATH（本機 ADC JSON 路徑）；AIRFLOW_PORT 預設 8081
docker compose --env-file orchestration/.env -f orchestration/compose.yml build airflow
docker compose --env-file orchestration/.env -f orchestration/compose.yml up -d
```

瀏覽器開啟 http://localhost:8081，帳號 `admin`、密碼 `admin`。Postgres 密碼也是 `admin`。這是本機自用的簡單設定，寫在 `compose.yml`：網頁只綁定 127.0.0.1，Postgres 不對外開埠。要給別人用或部署到伺服器前，必須改成從 `.env` 讀取的強密碼。

```powershell
# 新建立的 DAG 預設暫停，第一次要解除暫停才會開始每日排程
docker exec housing-airflow-airflow-1 airflow dags unpause taipei_housing_pipeline
# 手動觸發（只判斷，需要才跑）；加 force 則強制全量
docker exec housing-airflow-airflow-1 airflow dags trigger taipei_housing_pipeline
docker exec housing-airflow-airflow-1 airflow dags trigger taipei_housing_pipeline --conf '{"force": true}'
```

解除暫停時，Airflow 會依排程補建最近一次的執行（例如前一天中午那次），並立刻開始判斷。

停止服務並保留 Airflow 資料庫：

```powershell
docker compose --env-file orchestration/.env -f orchestration/compose.yml down
```

不要加 `--volumes`，否則會刪除 Airflow metadata。

## 映像與掛載

- 映像以官方 `apache/airflow:3.4.0-python3.12` 為基底，專案 runtime 裝在 `/opt/housing-venv`，受 `requirements.lock` 約束；uv 從 PyPI 安裝固定版本 0.13.6，不依賴 ghcr.io。
- 唯讀掛載：dags、scripts、config、analytics_bigquery、本機 ADC 憑證。可寫：data（landing 快取、dbt target 與 logs）。
- Build context 由 `.dockerignore` 限定只有依賴檔與 Dockerfile，原始資料、Windows `.venv` 與憑證不會進入映像。
- Airflow 容器上限 4 GB 記憶體、2 CPU；PostgreSQL 512 MB。

## 驗收紀錄（2026-11-17）

| 項目 | 結果 |
| --- | --- |
| DAG 載入 | `list-import-errors` 為空；以 DeprecationWarning 視為錯誤仍可載入 |
| 12 季全量執行 | 26 個任務全部成功，229 秒（下載使用既有快取；dbt 約 76 秒） |
| 同參數重跑 | 成功；raw 73,133、staging 73,133、fact 30,756、agg 360 列，與前次相同 |
| 未發布季別 115S4 | fetch 第 1 次嘗試即失敗（官方回 HTML），未重試，下游未執行；同時證明容器可連到官方網站 |
| 格式錯誤季別 abc | resolve_seasons 失敗，下游未執行 |
| medallion 分層（11-18） | 全量執行成功，223 秒；dbt 寫入 housing_silver、housing_gold |
| Gold 分析表（11-18） | 全量執行成功，343 秒；dbt build 78 項通過（7 個模型），遠低於 build_models 的 15 分鐘上限 |

尚未做的部分：展示版本切換與回復機制（dbt 直接更新 housing_gold）、正式部署、通知，以及 Airflow 端的自動排程。
