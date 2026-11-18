"""確保季別清單與分析期間只有一個真相來源：config/seasons.json。"""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'config/seasons.json').read_text(encoding='utf-8'))


def test_dbt_vars_match_seasons_config():
    project = yaml.safe_load(
        (ROOT / 'analytics_bigquery/dbt_project.yml').read_text(encoding='utf-8')
    )
    variables = project['vars']
    assert variables['seasons'] == CONFIG['seasons']
    assert variables['min_transaction_date'] == CONFIG['min_transaction_date']
    assert variables['as_of_date'] == CONFIG['as_of_date']
    assert 'raw_snapshot' not in variables


def test_seasons_config_is_sorted_and_unique():
    seasons = CONFIG['seasons']
    assert seasons == sorted(set(seasons))
    assert CONFIG['min_transaction_date'] < CONFIG['as_of_date']


def test_dag_reads_config_instead_of_hardcoding_seasons():
    dag = (ROOT / 'orchestration/dags/housing_pipeline.py').read_text(encoding='utf-8')
    assert 'seasons.json' in dag
    assert not any(season in dag for season in CONFIG['seasons'])
    assert CONFIG['as_of_date'] not in dag and CONFIG['min_transaction_date'] not in dag


def test_compose_no_longer_requires_local_zip():
    compose = (ROOT / 'orchestration/compose.yml').read_text(encoding='utf-8')
    assert 'HOUSING_ZIP_PATH' not in compose
    assert 'AIRFLOW_PORT' in compose  # 埠號可由 .env 設定，避開既有服務
    assert 'HOUSING_ZIP_PATH' not in (ROOT / 'orchestration/.env.example').read_text(
        encoding='utf-8'
    )


def test_runtime_requirements_list_requests():
    runtime = (ROOT / 'orchestration/requirements-runtime.txt').read_text(encoding='utf-8').split()
    assert 'requests' in runtime


# ---------- BigQuery medallion 分層 ----------


def _yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding='utf-8'))


def test_dbt_layers_map_to_medallion_datasets():
    models = _yaml('analytics_bigquery/dbt_project.yml')['models']['taiwan_housing_bigquery']
    assert models['staging']['+schema'] == 'silver'
    assert models['marts']['+schema'] == 'gold'


def test_dbt_source_reads_bronze_dataset():
    source = _yaml('analytics_bigquery/models/staging/sources.yml')['sources'][0]
    assert source['name'] == 'bronze'
    assert source['schema'] == 'housing_bronze'


def test_generate_schema_name_prefixes_non_prod_targets():
    # prod 寫入 housing_<層>；其他 target（例如 ci）加前綴，避免覆蓋正式資料
    macro = (ROOT / 'analytics_bigquery/macros/generate_schema_name.sql').read_text(
        encoding='utf-8'
    )
    assert 'macro generate_schema_name' in macro
    assert "'housing_'" in macro and "'prod'" in macro
    profiles = _yaml('analytics_bigquery/profiles.yml')['taiwan_housing_bigquery']
    assert profiles['target'] == 'prod'
    assert 'prod' in profiles['outputs']


def test_environment_check_test_model_removed():
    assert not (ROOT / 'analytics_bigquery/models/environment_check.sql').exists()
    assert not (ROOT / 'analytics_bigquery/models/schema.yml').exists()


def test_dbt_project_and_profiles_are_ascii():
    # dbt 在 Windows 以系統編碼（cp950）讀這兩個檔案計算雜湊，非 ASCII 字元會讓 dbt 崩潰
    for path in ['analytics_bigquery/dbt_project.yml', 'analytics_bigquery/profiles.yml']:
        assert (ROOT / path).read_bytes().isascii(), path


def test_ci_runs_lint_format_and_tests():
    ci = _yaml('.github/workflows/ci.yml')
    commands = ' '.join(step.get('run', '') for job in ci['jobs'].values() for step in job['steps'])
    assert 'ruff check' in commands
    assert 'black --check' in commands
    assert 'pytest' in commands
    # push 只在 main 觸發，PR 由 pull_request 觸發，避免同一次變更跑兩次
    triggers = ci[True] if True in ci else ci['on']  # PyYAML 會把 on 解析成 True
    assert triggers['push']['branches'] == ['main']
    assert 'pull_request' in triggers


# ---------- 每日排程與自動更新 ----------


def _dag_call_keywords() -> dict:
    """以 AST 取出 DAG(...) 呼叫的關鍵字參數原始碼（本機沒有安裝 Airflow，無法 import DAG）。"""
    import ast

    source = (ROOT / 'orchestration/dags/housing_pipeline.py').read_text(encoding='utf-8')
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'DAG':
            return {kw.arg: ast.unparse(kw.value) for kw in node.keywords}
    raise AssertionError('DAG(...) call not found')


def test_dag_is_scheduled_daily_in_taipei_time():
    keywords = _dag_call_keywords()
    assert keywords['dag_id'] == "'taipei_housing_pipeline'"
    assert keywords.get('schedule') not in (None, 'None')
    assert 'Asia/Taipei' in keywords['schedule']
    assert keywords['catchup'] == 'False'
    assert keywords['max_active_runs'] == '1'


def test_config_has_scheduling_settings():
    assert CONFIG['as_of_lag_days'] == 160
    assert CONFIG['refresh_before_expiry_days'] > 0


def test_compose_restarts_services_after_reboot():
    services = _yaml('orchestration/compose.yml')['services']
    for name in ('postgres', 'airflow'):
        assert services[name].get('restart') == 'unless-stopped', name


def test_build_metadata_model_records_successful_build():
    sql = (ROOT / 'analytics_bigquery/models/marts/build_metadata.sql').read_text(encoding='utf-8')
    # 必須依賴所有 gold 彙總表，確保只有在上游全部建好、測試通過後才寫入
    for model in [
        'agg_district_month_price',
        'agg_district_quarter_price',
        'agg_district_segment_price',
        'agg_district_budget',
        'agg_city_month_market',
    ]:
        assert f"ref('{model}')" in sql, model
