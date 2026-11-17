-- 住宅交易的建物型態都必須對應到已知分組；落到 other 代表前綴比對需要更新
select source_row_key, building_type
from {{ ref('fct_housing_deal') }}
where is_residential and building_type_group = 'other'
