select exclusion_reason, count(*) as row_count
from {{ ref('stg_lvr_sale') }} group by exclusion_reason
