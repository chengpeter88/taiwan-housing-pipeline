with ranked as (
    select *, row_number() over (
        partition by city_code, serial_no
        order by source_date desc, batch_id desc, row_number desc
    ) as revision_rank
    from {{ source('raw', 'lvr_sale') }}
), typed as (
    select *,
        case when regexp_full_match(transaction_roc, '[0-9]{7}') then
            cast(try_strptime(cast(try_cast(substr(transaction_roc, 1, 3) as integer) + 1911 as varchar)
                 || substr(transaction_roc, 4, 4), '%Y%m%d') as date)
        end as transaction_date,
        try_cast(total_price as double) as total_price_ntd,
        try_cast(building_area as double) as building_area_sqm,
        try_cast(parking_price as double) as parking_price_ntd,
        try_cast(parking_area as double) as parking_area_sqm
    from ranked where revision_rank = 1
), flagged as (
    select *,
        total_price_ntd - parking_price_ntd as housing_price_ntd,
        building_area_sqm - parking_area_sqm as housing_area_sqm,
        transaction_target in ('房地(土地+建物)', '房地(土地+建物)+車位') as is_housing,
        contains(notes, '親友、員工、共有人或其他特殊關係') as is_special_deal,
        contains(notes, '預售屋、或土地及建物分件登記') as is_presale_or_separate,
        transaction_date is null as is_invalid_date,
        transaction_date > cast('{{ var("as_of_date") }}' as date) as is_future_date
    from typed
)
select *, case
    when not is_housing then 'non_housing'
    when is_special_deal then 'special_relationship'
    when is_presale_or_separate then 'presale_or_separate'
    when is_invalid_date then 'invalid_date'
    when is_future_date then 'future_date'
    when transaction_date < cast('{{ var("min_transaction_date") }}' as date) then 'before_window'
    when housing_price_ntd is null or housing_area_sqm is null
      or housing_price_ntd <= 0 or housing_area_sqm <= 0
      or parking_price_ntd < 0 or parking_area_sqm < 0 then 'invalid_price_or_area'
    when nullif(trim(district), '') is null then 'missing_district'
    else 'eligible'
end as exclusion_reason
from flagged
