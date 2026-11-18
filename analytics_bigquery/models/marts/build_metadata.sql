-- 最近一次成功建置的狀態（單列）。Airflow 每天比對它與目前期望的季別／日期，決定要不要重跑。
-- 以 depends_on 依賴所有 gold 彙總表：dbt build 時它最後才建，上游任何測試失敗就會被跳過，
-- 所以這張表存在就代表「該狀態已完整建置成功」。
-- depends_on: {{ ref('agg_district_month_price') }}
-- depends_on: {{ ref('agg_district_quarter_price') }}
-- depends_on: {{ ref('agg_district_segment_price') }}
-- depends_on: {{ ref('agg_district_budget') }}
-- depends_on: {{ ref('agg_city_month_market') }}
select
    '{{ var("seasons") | join(",") }}' as seasons,
    date('{{ var("min_transaction_date") }}') as min_transaction_date,
    date('{{ var("as_of_date") }}') as as_of_date,
    current_timestamp() as built_at
