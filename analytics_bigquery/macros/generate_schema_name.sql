{#-
    依 medallion 分層決定 BigQuery dataset 名稱。
    - 模型以 +schema 指定層（silver、gold），對應 housing_silver、housing_gold。
    - target 為 prod 時直接使用；其他 target（例如 ci）加上 target 名稱前綴，
      例如 ci_housing_gold，避免測試環境覆蓋正式資料。
    - bronze 由 Python 載入器寫入，不經過 dbt。
-#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set layer = custom_schema_name if custom_schema_name is not none else 'gold' -%}
    {%- set dataset = 'housing_' ~ (layer | trim) -%}
    {%- if target.name == 'prod' -%}
        {{ dataset }}
    {%- else -%}
        {{ target.name }}_{{ dataset }}
    {%- endif -%}
{%- endmacro %}
