# Airflow 第一版 DAG 草稿

狀態：只完成程式與 Python 語法檢查，尚未安裝 Airflow、載入 DagBag 或在容器實跑。不能宣稱排程已完成。

目標 Airflow 3，使用官方 TaskFlow API，不使用 Google provider。DAG 呼叫現有的 pandas／BigQuery 載入器與 dbt，不複製 SQL 清理邏輯。

## 執行流程

`validate_source → load_raw → build_models`

驗證任務先將來源固定至本機雜湊目錄，XCom 只傳雜湊與筆數。載入任務使用固定檔案，dbt 使用同一雜湊指定 raw_snapshot，不會默默讀取 dbt_project.yml 的舊來源。錯誤以非零程序退出碼傳遞，後續任務不執行。

目前 schedule=None，只接受手動觸發。每次最多一個 DAG run、一個 task，失敗最多重試兩次。重試可能再發出 BigQuery 工作及消耗查詢額度；raw 以相同來源表覆寫，不累加資料。人工命令不可同時執行，因為 data、dbt target 與 housing_dev 為共用資源。

## 啟動前要準備的環境

- Linux Docker 容器中的 Airflow 3；不安裝進 Windows 的專案 .venv。
- 專案掛載至 `/opt/housing`，輸入 ZIP 唯讀掛載至 `/opt/input/lvr_landcsv.zip`。
- 在容器內建立專案 Python 3.12 環境 `/opt/housing-venv`，以 requirements.lock 安裝。不要掛載 Windows .venv 當作 Linux 環境。
- Airflow 所有執行任務的 worker 必須看得到同一個專案與 data 目錄；data 及 dbt target/logs 可寫。
- 容器使用本機 ADC 的唯讀掛載，GOOGLE_APPLICATION_CREDENTIALS 指向容器內憑證檔；不得將憑證寫進 image、Git 或 DAG。GOOGLE_CLOUD_PROJECT 設為 taiwan-household。
- HOUSING_PROJECT_DIR、HOUSING_PYTHON 可調整容器路徑。BigQuery 專案、dataset 與 US 區域沿用現有設定。

Docker Compose 與 Dockerfile 已建立，Compose config 靜態檢查通過；尚未 build 映像或啟動容器，版本依賴與 DAG runtime 仍待實跑驗證。目前沒有任何自動排程啟用。

## Docker 開發環境

使用官方 Airflow 3.4.0 Python 3.12 映像，以 standalone 在單一容器啟動開發所需程序，另加 PostgreSQL 16。此方式僅適用本機練習，不是正式部署。LocalExecutor 最多一個任務，Airflow 容器限制 4 GB 記憶體及 2 CPU，PostgreSQL 限制 512 MB。限制不是資源足夠的保證，啟動前需確認 Docker 與主機可用記憶體；不足時先不要啟動。

專案 runtime 放在容器的 /opt/housing-venv，以 uv 安裝並受 requirements.lock 約束。只裝 BigQuery 管線直接需要的套件，未額外要求 Google provider、DuckDB、Streamlit 或 pytest；dbt-bigquery 的間接依賴仍會安裝。Airflow 自己的環境保持由官方映像管理。

只有必要的 scripts、config、analytics_bigquery 唯讀掛載，data 可寫。dbt target 與 logs 轉到 data 下。Docker build context 透過 .dockerignore 限定只有依賴檔與 Dockerfile，原始資料、Windows .venv 與憑證不會被 COPY 到 image。

### 準備與靜態檢查

在專案根目錄手動執行；若 .env 已存在，保留它並檢查內容，不要覆蓋：

```powershell
Copy-Item orchestration/.env.example orchestration/.env
docker compose --env-file orchestration/.env -f orchestration/compose.yml config --quiet
```

編輯 .env，確認 ADC 與 ZIP 是存在的檔案路徑；POSTGRES_PASSWORD 使用本機開發密碼，為避免連線字串編碼問題可用英數字。ADC 唯讀掛載，不要把內容貼進 .env。Docker 不會自動建立缺少的 ADC 或 ZIP 檔案。

### 下一階段才執行的啟動步驟

以下會下載／建置映像並啟動服務，本輪尚未執行：

```powershell
docker compose --env-file orchestration/.env -f orchestration/compose.yml build airflow
docker compose --env-file orchestration/.env -f orchestration/compose.yml up -d
docker compose --env-file orchestration/.env -f orchestration/compose.yml logs --tail 80 airflow
```

瀏覽器開啟 http://localhost:8080。SimpleAuthManager 使用 admin 帳號及首次啟動產生的密碼，依容器啟動輸出取得，不是固定 airflow/airflow。網頁只綁定本機 127.0.0.1，DAG 預設暫停且沒有排程。確認無 import error 後，才手動觸發 DAG；觸發會實際讀寫 BigQuery。

停止服務並保留資料庫：

```powershell
docker compose --env-file orchestration/.env -f orchestration/compose.yml down
```

不要任意加 --volumes，否則會刪除 Airflow metadata 與相關狀態。

## 手動觸發參數

zip_path 為容器路徑；min_date 是分析起日；as_of 是固定分析截止日，不會自動取今天。預設重現現有快照，其他批次需指定相符的日期窗口。

## 驗收清單

1. DagBag 可以載入，沒有 import error。
2. 手動執行三個任務全綠，來源雜湊與 dbt 使用的表一致。
3. 同檔重跑兩次，raw 與分析筆數不累加。
4. 壞 ZIP 在驗證步驟失敗，後續不執行。
5. dbt 測試失敗時 DAG 失敗。

第 5 項仍不是正式發布保護：dbt 目前直接更新 housing_dev。展示版本切換與回復機制尚未實作，這個 DAG 不含發布任務，也尚未包含官方自動下載器。

參考：https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/params.html
