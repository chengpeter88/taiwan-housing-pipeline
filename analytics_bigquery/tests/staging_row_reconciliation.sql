select 1 as mismatch
from (select 1)
where (select count(*) from {{ ref('stg_lvr_sale') }})
   <> (select count(*) from {{ source('housing_raw', 'lvr_sale') }})
