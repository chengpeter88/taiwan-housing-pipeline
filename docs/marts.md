# 交易明細與住宅月彙總

2026-11-16 驗證完成。所有模型目前位於 housing_dev，尚未建立正式展示發布閘門。

- fct_housing_deal：2,393 筆合格房地交易，包含各用途；is_residential 為真的住宅交易為 1,840 筆。
- agg_district_month_price：住宅交易按城市、行政區與交易月份分組，共 73 列。每列含成交筆數與逐筆單價的精確中位數。
- 此快照、此分析窗口的住宅整體中位數為每坪約 796,252 元，即 79.63 萬元；不是舊版全用途的 80.17 萬元。

每坪單價 = 扣車位後價格 / 扣車位後平方公尺 × 3.30579。面積坪數 = 平方公尺 / 3.30579。金額面積使用 NUMERIC，避免先轉浮點數再計算。

沒有成交的行政區月份不補零，沒有額外剔除離群價格。小樣本及屋齡、型態組成差異會影響中位數，因此不能解讀成房價指數。跨月篩選時必須回到 fact 的逐筆住宅交易重算，不可平均或再取月中位數的中位數。

驗證：2 個模型與 9 個資料測試通過，涵蓋鍵唯一、非空、價格公式、月粒度及筆數對帳。另外以 pandas 對全部 73 組獨立重算，筆數一致，中位數差異小於 0.01 元。

```powershell
.venv\Scripts\dbt.exe build --project-dir analytics_bigquery --profiles-dir analytics_bigquery --select fct_housing_deal+
```

此指令依賴已存在的 staging。若變更來源或日期窗口，請先重建 staging，或使用 `--select +agg_district_month_price` 重建其上游。來源與日期參數見 dbt_project.yml。
