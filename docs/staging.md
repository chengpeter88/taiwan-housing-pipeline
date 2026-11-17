# BigQuery staging

模型：`taiwan-household.housing_dev.stg_lvr_sale`。來源為 wildcard 表 `housing_raw.lvr_season_a_*`，只納入 `var('seasons')` 列出的季別（預設值與 `config/seasons.json` 相同，pytest 會檢查兩者一致）。

```powershell
.venv\Scripts\dbt.exe build --project-dir analytics_bigquery --profiles-dir analytics_bigquery --select stg_lvr_sale
```

## 分析期間

`min_transaction_date = 2023-10-01`、`as_of_date = 2026-03-31`，依據如下：

- 左界：最早的 112S4 從 2023-09-11 開始收錄登記，所以 9 月以前成交、更早登記的案件不在資料內。從 10 月起算，避免第一個月少算。
- 右界：最後一季 115S3 的登記截止日是 2026-09-10。以季末推估，成交後 120 天內被收錄的比例約 69%，180 天約 93%。2026-03 距截止日約 163 天，當月筆數（661）與前幾個月相當；4 月以後明顯偏低，所以不納入。
- 截止日是固定值，讓結果可以重現。新季別發布後，要同時更新 `config/seasons.json` 與 `dbt_project.yml`。

## 規則

- 一列對應來源一列，以 `source_sha256:source_row_number` 為唯一鍵，不刪除任何交易。
- 民國日期支援 6 位數（民國 99 年前，例如 `980806`）與 7 位數，無效日期為 NULL。
- 同城市編號在納入的季別中出現多次時，標記 `duplicate_serial_review` 並暫停納入價格分析，不自行挑一筆。目前 12 季沒有重複。
- `exclusion_reason` 依固定順序給每列一個主要原因，`is_price_eligible` 表示合格房地交易，`is_residential_price_eligible` 另外要求主要用途為住家用。

| exclusion_reason | 筆數 |
| --- | ---: |
| eligible | 30,756 |
| non_housing | 13,181 |
| presale_or_separate | 10,071 |
| special_relationship | 7,890 |
| unclear_parking | 4,168 |
| outside_window | 3,723 |
| future_date | 3,344 |

`future_date` 是成交日晚於 2026-03-31 的案件，屬於刻意排除的近期資料，不是錯誤。

## 測試

- dbt unit tests（`models/staging/unit_tests.yml`）以假資料覆蓋全部 12 種排除原因、跨季重複編號、6 位數日期與不在清單內的季別，因為真實資料中有些分支筆數為 0。
- 資料測試：唯一鍵、`exclusion_reason` 與 `parking_status` 允許值、季別完整性、表名後綴與 `season` 欄位一致、staging 與來源列數對帳。

車位無法拆算者排除於價格分析；特殊交易以已知完整用語比對，不涵蓋所有自由文字（例如「瑕疵物件」仍會納入）。
