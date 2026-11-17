{#- 將 var('seasons') 轉成 SQL 的 IN 清單，例如 ('115S1', '115S2')。
    staging 與資料測試共用，確保兩邊篩選的季別一致。 -#}
{% macro season_list() -%}
    ({%- for season in var('seasons') -%}
        '{{ season }}'{{ ', ' if not loop.last }}
    {%- endfor -%})
{%- endmacro %}
