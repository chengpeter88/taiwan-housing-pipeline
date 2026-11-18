-- build_metadata 必須剛好一列，Airflow 才能正確讀出上次建置的狀態
select count(*) as row_count
from {{ ref('build_metadata') }}
having count(*) != 1
