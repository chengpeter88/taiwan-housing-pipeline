{#- 民國日期字串（6 或 7 碼，如 '980806'、'1150115'）轉 DATE。
    不足 7 碼左補 0；年份 = 前 3 碼 + 1911；格式不符或日期不存在（如 6/31）回傳 NULL。
    staging 的交易日與完工日共用。 -#}
{% macro roc_to_date(expr) -%}
    case when regexp_contains(trim({{ expr }}), r'^[0-9]{6,7}$') then
        safe.parse_date('%Y%m%d', concat(
            cast(safe_cast(substr(lpad(trim({{ expr }}), 7, '0'), 1, 3) as int64) + 1911 as string),
            substr(lpad(trim({{ expr }}), 7, '0'), 4, 4)))
    end
{%- endmacro %}
