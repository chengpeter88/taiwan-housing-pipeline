-- staging 一列對應來源一列：只比對 var('seasons') 指定的季
select 1 as mismatch
from (select 1)
where (select count(*) from {{ ref('stg_lvr_sale') }})
   <> (select count(*) from {{ source('bronze', 'lvr_season_a') }}
       where season in {{ season_list() }})
