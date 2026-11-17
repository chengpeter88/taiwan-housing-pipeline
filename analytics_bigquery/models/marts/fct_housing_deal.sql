-- 逐筆 fact：只做業務衍生（分組、分帶、屋齡），型別轉換已在 silver 完成。
with base as (
    select source_row_key, source_sha256, city_code, district, serial_no,
        transaction_date, date_trunc(transaction_date, month) as transaction_month,
        date_trunc(transaction_date, quarter) as transaction_quarter,
        main_use, building_type, is_residential, parking_status,
        housing_price as housing_price_ntd, housing_area as housing_area_sqm,
        housing_area / numeric '3.30579' as housing_area_ping,
        safe_divide(housing_price, housing_area) * numeric '3.30579' as price_per_ping,
        total_price / 10000 as total_price_incl_parking_10k,
        housing_price / 10000 as housing_price_10k,
        completion_date, room_count, has_elevator, has_management,
        parking_status = 'separately_reported' as is_parking_included,
        -- 建物型態以前綴歸類（來源文字帶括號說明，且可能有前導空白）
        case
            when starts_with(trim(building_type), '住宅大樓') then 'tower'
            when starts_with(trim(building_type), '華廈') then 'mansion'
            when starts_with(trim(building_type), '公寓') then 'apartment'
            when starts_with(trim(building_type), '透天厝') then 'townhouse'
            else 'other'
        end as building_type_group,
        -- 屋齡 = 完整年數；完工日為空或晚於交易日則未知
        case
            when completion_date is null or completion_date > transaction_date then null
            else date_diff(transaction_date, completion_date, year)
                - if(format_date('%m%d', transaction_date) < format_date('%m%d', completion_date), 1, 0)
        end as building_age_years
    from {{ ref('stg_lvr_sale') }}
    where is_price_eligible
)
select *,
    case
        when building_age_years is null then 'unknown'
        when building_age_years < 10 then '0-10'
        when building_age_years < 30 then '10-30'
        else '30+'
    end as age_band,
    -- 坪數帶依不含車位的坪數，左閉右開
    case
        when housing_area_ping < 15 then '<15'
        when housing_area_ping < 25 then '15-25'
        when housing_area_ping < 35 then '25-35'
        when housing_area_ping < 50 then '35-50'
        else '50+'
    end as area_band,
    -- 預算帶依含車位總價（萬元），左閉右開
    case
        when total_price_incl_parking_10k < 1000 then '<1000'
        when total_price_incl_parking_10k < 1500 then '1000-1500'
        when total_price_incl_parking_10k < 2000 then '1500-2000'
        when total_price_incl_parking_10k < 3000 then '2000-3000'
        when total_price_incl_parking_10k < 5000 then '3000-5000'
        else '5000+'
    end as budget_band
from base
