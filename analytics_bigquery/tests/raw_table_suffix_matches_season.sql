-- 每季一表：表名後綴必須等於資料列的 season，防止季檔被載進錯的表
select _table_suffix as table_suffix, season, count(*) as rows_mismatched
from {{ source('housing_raw', 'lvr_season_a') }}
where _table_suffix != season
group by table_suffix, season
