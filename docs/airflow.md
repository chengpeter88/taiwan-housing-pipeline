# Airflow DAG

狀態：2026-11-17 已在 Docker（Airflow 3.4.0、Python 3.12）實跑驗證。只接受手動觸發，沒有啟用任何排程。

DAG：`orchestration/dags/housing_pipeline.py`，dag_id `taipei_housing_manual`。使用 TaskFlow API，不需要 Google provider；載入與轉換都呼叫專案既有的載入器與 dbt，不複製 SQL 邏輯。

## 執行流程

`resolve_seasons → fetch_validate（每季一個）→ load_season（每季一個）→ build_models`

- `resolve_seasons`：驗證季別格式、去重排序，檢查 min_date 不晚於 as_of。
- `fetch_validate`：下載（或使用快取）並驗證該季，XCom 只傳季別、sha256 與筆數。
- `load_season`：用驗證過的同一份 `data/landing/<sha256>/source.zip` 載入 BigQuery。
- `build_models`：全部季別載入後執行 `dbt build`。季別取自 `resolve_seasons` 的 XCom，與載入的清單一致。

## 參數

| 參數 | 預設 | 說明 |
| --- | --- | --- |
| seasons | `config/seasons.json` 的清單 | 要處理的季別 |
| min_date | `config/seasons.json` | 分析起日 |
| as_of | `config/seasons.json` | 分析截止日，固定值，不會自動取今天 |
| refresh | false | 忽略快取，重新下載官方檔 |

DAG 原始碼不寫死任何季別或日期，新季別只需改 `config/seasons.json` 與 `dbt_project.yml`（pytest 會檢查兩者一致）。

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
# 指令列觸發（會實際下載並讀寫 BigQuery）
docker exec housing-airflow-airflow-1 airflow dags unpause taipei_housing_manual
docker exec housing-airflow-airflow-1 airflow dags trigger taipei_housing_manual
```

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

尚未做的部分：展示版本切換與回復機制（dbt 直接更新 housing_dev）、正式部署、通知，以及 Airflow 端的自動排程。
