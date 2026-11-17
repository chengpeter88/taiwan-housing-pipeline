# 本機開發環境

專案統一使用 `.venv`。Python 3.12 取代已不受目前 Google client 支援的 Python 3.10。依賴清單在 `requirements.txt`，跨平台解析的固定版本在 `requirements.lock`；由 uv 安裝。

## 現有電腦啟用

```powershell
cd E:\coding\taiwan-housing-pipeline
. .\scripts\activate.ps1
python --version
uv pip check
gcloud --version
```

啟用腳本只調整目前 PowerShell 的 PATH 與 uv cache，並啟用原 `.venv`。VS Code 開啟本專案資料夾時，設定預設解譯器為 `.venv/Scripts/python.exe`，新終端機可找到 gcloud。若 VS Code 已記住其他解譯器，需手動重新選擇這個路徑。

Google Cloud CLI 安裝在 `E:/coding/tools/google-cloud-sdk`，版本 593.0.0；來源是 Google 官方 Windows ZIP，下載後驗證官方 SHA256。Python 3.12 安裝在 `E:/coding/tools/python`。這些工具不屬於 Git 專案內容。

## 依賴用途

- `dbt-core`、`dbt-bigquery`：正式 BigQuery 模型與測試。
- `google-cloud-bigquery`、`pandas`、`pyarrow`：官方載入介面與資料處理。
- `requests`：下載官方季檔。
- `pytest`：Python 測試。

Google AI、Dataproc 等相依套件由 dbt-bigquery 的依賴鏈帶入；安裝套件不會啟用對應雲端服務。

## 重建與更新

新電腦先安裝 uv，執行：

```powershell
uv venv --python 3.12
uv pip sync requirements.lock --python .venv/Scripts/python.exe
```

變更依賴時先編輯 `requirements.txt`，再執行：

```powershell
uv pip compile requirements.txt --python .venv/Scripts/python.exe --universal --output-file requirements.lock
uv pip sync requirements.lock --python .venv/Scripts/python.exe
uv pip check --python .venv/Scripts/python.exe
.venv\Scripts\python.exe -m pytest -q
```

## GCP 登入與驗證

2026-11-15 已在本機完成，專案 ID 為 `taiwan-household`。新電腦或重新登入時，在已啟用環境的互動 PowerShell 依序執行：

```powershell
gcloud init
gcloud auth application-default login
gcloud auth application-default set-quota-project taiwan-household
gcloud config list
```

- `gcloud init`：設定 CLI 帳號與專案。選專案時要填 project ID（如 `taiwan-household`），不是 project number。
- `application-default login`：建立 Python／dbt 使用的 ADC。登入由本人在瀏覽器完成，不需複製 token 或下載 service account key。
- `set-quota-project`：API 用量與配額記在本專案，避免 quota project 警告。

ADC 存在 `%APPDATA%\gcloud\application_default_credentials.json`，與程式碼所在磁碟無關，client library 會自動讀取。此檔等同登入權限，不可複製進專案或 commit。

### 最小連線驗證

```powershell
.venv\Scripts\python.exe -c "from google.cloud import bigquery; c=bigquery.Client(project='taiwan-household'); print(list(c.query('SELECT 1 AS ok').result())[0].ok)"
```

輸出 `1` 表示 ADC、專案與 BigQuery 權限正常。此查詢不讀資料表、不建立資源。2026-11-15 已通過。

### dbt BigQuery 設定

dbt 專案在 `analytics_bigquery/`，連線設定是同目錄的 `profiles.yml`：

| 設定 | 值 | 說明 |
| --- | --- | --- |
| method | `oauth` | 使用 ADC，檔案內無機密 |
| project | `taiwan-household` | GCP 專案 |
| target | `prod` | 寫入 `housing_silver`、`housing_gold`；其他 target 自動加前綴，見 `macros/generate_schema_name.sql` |
| location | `US` | 與 `housing_bronze` 相同；dataset 建立後不可更改 |

```powershell
.venv\Scripts\dbt.exe debug --project-dir analytics_bigquery --profiles-dir analytics_bigquery
```

輸出 `All checks passed!` 表示 dbt 可以連上 BigQuery。

官方參考：https://docs.cloud.google.com/docs/authentication/set-up-adc-local-dev-environment
