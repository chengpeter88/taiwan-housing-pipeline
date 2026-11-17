with typed as (
    select *,
        concat(source_sha256, ':', cast(source_row_number as string)) as source_row_key,
        {{ roc_to_date('transaction_date_roc') }} as transaction_date,
        {{ roc_to_date('completion_date_roc') }} as completion_date,
        safe_cast(trim(rooms) as int64) as room_count,
        case trim(elevator) when '有' then true when '無' then false end as has_elevator,
        case trim(management) when '有' then true when '無' then false end as has_management,
        safe_cast(total_price_ntd as numeric) as total_price,
        safe_cast(building_area_sqm as numeric) as building_area,
        safe_cast(parking_price_ntd as numeric) as parking_price,
        safe_cast(parking_area_sqm as numeric) as parking_area,
        count(*) over (partition by city_code, serial_no) as serial_occurrences
    from {{ source('bronze', 'lvr_season_a') }}
    where season in {{ season_list() }}
), flagged as (
    select *,
        total_price - parking_price as housing_price,
        building_area - parking_area as housing_area,
        coalesce(transaction_target in ('房地(土地+建物)', '房地(土地+建物)+車位'), false) as is_housing,
        coalesce(trim(main_use) = '住家用', false) as is_residential,
        strpos(coalesce(notes, ''), '親友、員工、共有人或其他特殊關係') > 0 as is_special_deal,
        strpos(coalesce(notes, ''), '預售屋、或土地及建物分件登記') > 0 as is_presale_or_separate,
        transaction_date is null as is_invalid_date,
        coalesce(transaction_date > date('{{ var("as_of_date") }}'), false) as is_future_date,
        coalesce(transaction_date between date('{{ var("min_transaction_date") }}')
                 and date('{{ var("as_of_date") }}'), false) as is_in_window,
        case
            when transaction_target = '房地(土地+建物)' and parking_price = 0
                and parking_area = 0 and trim(coalesce(parking_type, '')) = '' then 'no_parking'
            when transaction_target = '房地(土地+建物)+車位'
                and parking_price > 0 and parking_area > 0 then 'separately_reported'
            when transaction_target in ('房地(土地+建物)', '房地(土地+建物)+車位') then 'unclear_or_inconsistent'
            else 'not_housing'
        end as parking_status
    from typed
), classified as (
    select *, case
        when nullif(trim(serial_no), '') is null then 'missing_serial'
        when serial_occurrences > 1 then 'duplicate_serial_review'
        when not is_housing then 'non_housing'
        when is_special_deal then 'special_relationship'
        when is_presale_or_separate then 'presale_or_separate'
        when is_invalid_date then 'invalid_date'
        when is_future_date then 'future_date'
        when not is_in_window then 'outside_window'
        when housing_price is null or housing_area is null or housing_price <= 0 or housing_area <= 0
            or parking_price < 0 or parking_area < 0 then 'invalid_price_or_area'
        when parking_status = 'unclear_or_inconsistent' then 'unclear_parking'
        when nullif(trim(district), '') is null then 'missing_district'
        else 'eligible'
    end as exclusion_reason
    from flagged
)
select *, exclusion_reason = 'eligible' as is_price_eligible,
    exclusion_reason = 'eligible' and is_residential as is_residential_price_eligible
from classified
