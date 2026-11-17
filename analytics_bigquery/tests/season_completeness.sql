-- var('seasons') 列出的每一季都必須出現在 staging；缺任何一季就失敗
select expected_season
from unnest([{% for season in var('seasons') %}'{{ season }}'{{ ', ' if not loop.last }}{% endfor %}])
    as expected_season
where expected_season not in (select distinct season from {{ ref('stg_lvr_sale') }})
