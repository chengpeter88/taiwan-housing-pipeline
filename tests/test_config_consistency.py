"""確保季別清單與分析期間只有一個真相來源：config/seasons.json。"""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'config/seasons.json').read_text(encoding='utf-8'))


def test_dbt_vars_match_seasons_config():
    project = yaml.safe_load((ROOT / 'analytics_bigquery/dbt_project.yml').read_text(encoding='utf-8'))
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
    assert 'HOUSING_ZIP_PATH' not in (ROOT / 'orchestration/.env.example').read_text(encoding='utf-8')


def test_runtime_requirements_list_requests():
    runtime = (ROOT / 'orchestration/requirements-runtime.txt').read_text(encoding='utf-8').split()
    assert 'requests' in runtime
