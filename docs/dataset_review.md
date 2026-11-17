# 臺北買賣季檔盤點與開工規格

> **歷史紀錄**：這是 2026-11-15 對單一季檔（114S4）的盤點，用來推導車位、住宅用途等清理規則。文中提到的 DuckDB 原型已移除；現行資料範圍為 12 季，見 [staging.md](staging.md) 與 [raw_loading.md](raw_loading.md)。

檢查日期：2026-11-15。正式架構仍以 BigQuery、dbt Core、後續 Airflow 與 Looker Studio 為準。現有 DuckDB 程式是先前本機原型，不代表 BigQuery 階段已完成。這次僅新增獨立 pandas 盤點腳本，沒有改寫既有展示資料。

## 資料來源與範圍

- 本機來源：`C:/Users/User/Downloads/lvr_landcsv.zip`。
- SHA256：`68315c006fa367834eaacf032ceccb22a114fc8798d87b8dac23cf5eb64030ab`。
- 236 個檔案，解壓總量 103,224,613 bytes。ZIP CRC 檢查通過。
- 本次逐列分析只涵蓋 `a_lvr_land_a.csv`，不是全國資料品質審核。
- UTF-8 BOM、中文表頭及一列英文欄位說明；資料列 5,819，33 個欄位。
- 買賣登記收錄期間為 2025-09-11 至 2025-12-10，不能視為官方發布日期或交易日期範圍。
- 實際交易日期範圍為 2015-08-27 至 2025-12-04。以 2026-11-15 為基準，未見無效或未來交易日期。
- 編號無空白、無重複；沒有完全重複列。此結論不證明跨期不重複。

## 車位資訊會影響分析資格

| 房地加車位的申報情形 | 筆數 |
| --- | ---: |
| 車位價格與面積皆大於零 | 1,282 |
| 價格為零、面積大於零 | 146 |
| 價格大於零、面積為零 | 67 |
| 價格與面積皆為零 | 137 |
| 合計 | 1,632 |

這些欄位沒有空值；風險來自零值的業務含義，單純 not_null 測試無法抓出來。350 筆含車位交易至少有一項為零，不能可靠地視為可拆算車位。另有 2,595 筆房地不含車位，車位價格與面積皆為零且車位類別空白。

以下是規則比較，不是已核定的正式統計：

| 分析口徑 | 筆數 | 每坪單價中位數 |
| --- | ---: | ---: |
| 舊規則，交易日起日 2025-06-01 | 2,658 | 約 80.17 萬元 |
| 舊規則加上車位資訊可拆算條件 | 2,393 | 約 80.55 萬元 |

舊樣本中 265 筆落在車位資訊不明類別。其餘 2,393 筆由不含車位 1,891 筆與可拆算車位 502 筆構成。嚴格篩選可能改變樣本組成，不能把中位數差異解讀成市場漲跌。

建議 staging 輸出 `parking_status`：`no_parking`、`separately_reported`、`unclear_or_inconsistent`、`not_housing`。價格與面積大於零仍不保證申報正確，需再檢查扣除後值與抽樣案例。

## 房地交易不等於住宅交易

2,393 筆的主要用途如下：住家用 1,840、商業用 310、辦公用 89、空白 63、其他 50、住商用 25、工業用 16。

建議正式 fact 保留各用途，住宅主指標明確篩選 `主要用途 = 住家用`，其他用途可切換查看，空白或混合用途不自動歸為住宅。前述 80.55 萬元是全部合格房地交易的中位數，不能標成住宅中位數。住宅的單價結果應在採用住宅口徑後另行計算。

## 使用常見套件的實作邊界

| 工作 | 採用工具 | 自己維護的內容 |
| --- | --- | --- |
| ZIP、雜湊、檔案路徑 | Python 標準庫 zipfile、hashlib、pathlib | 指定來源檔與大小限制 |
| 資料盤點、CSV 解析 | pandas | 欄位需求、旗標與盤點規則 |
| BigQuery 載入 | google-cloud-bigquery 官方 client | 明確 schema、批次 metadata、載入選項 |
| 正式清理與彙總 | dbt Core、dbt-bigquery | staging／mart SQL、schema.yml |
| 測試 | dbt tests、pytest | 業務邊界案例與驗收條件 |
| 後續排程 | Airflow 官方 Google provider | 任務依賴、重試與發布條件 |

不寫自製 CSV parser、SQL 執行器、排程器、ORM 或通用 ETL 框架。盤點程式的試算是規則探索；正式規則確定後，以 dbt SQL 作為唯一正式轉換來源，避免 Python 與 SQL 長期各維護一份業務邏輯。

## 下一個 coding 關卡

先完成 BigQuery 的小資料環境驗證，再做正式載入：確認 project ID、dataset location、本機登入及必要權限，載入幾筆資料並跑通一個 dbt model。環境驗證通過後，才做完整欄位對應、raw 載入、staging、品質閘門與發布。這次沒有建立雲端資源、沒有處理帳戶金鑰，也沒有執行完整雲端管線。

## 重跑盤點

在專案目錄執行：

```powershell
.venv\Scripts\python.exe scripts/profile_dataset.py C:\Users\User\Downloads\lvr_landcsv.zip --as-of 2026-11-15
.venv\Scripts\python.exe -m pytest tests/test_profile_dataset.py -q
```

使用專案的 `.venv` 即可，盤點腳本只需要 pandas。完整機器可讀結果寫入 `data/dataset_profile.json`，不含地址或逐筆交易資料。官方來源：https://plvr.land.moi.gov.tw/DownloadOpenData 。
