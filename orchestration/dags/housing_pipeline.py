"""Airflow 3 DAG draft. Manual runs only; no Google provider required."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess

from airflow.sdk import DAG, Param, get_current_context, task


def project_paths():
    root = Path(os.environ.get('HOUSING_PROJECT_DIR', '/opt/housing'))
    python = Path(os.environ.get('HOUSING_PYTHON', '/opt/housing-venv/bin/python'))
    return root, python


with DAG(
    dag_id='taipei_housing_manual',
    start_date=datetime(2026, 11, 16, tzinfo=timezone.utc),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    max_active_tasks=1,
    dagrun_timeout=timedelta(minutes=30),
    default_args={'retries': 2, 'retry_delay': timedelta(minutes=1),
                  'execution_timeout': timedelta(minutes=8)},
    params={
        'zip_path': Param('/opt/input/lvr_landcsv.zip', type='string', minLength=1),
        'min_date': Param('2025-06-01', type='string', format='date'),
        'as_of': Param('2026-11-16', type='string', format='date'),
    },
    tags=['housing', 'bigquery', 'manual'],
) as dag:

    @task
    def validate_source():
        params = get_current_context()['params']
        if params['min_date'] > params['as_of']:
            raise ValueError('min_date must not be after as_of')
        root, python = project_paths()
        result = subprocess.run(
            [str(python), '-m', 'scripts.load_bigquery', '--zip', params['zip_path'], '--validate-only'],
            cwd=root, check=True, capture_output=True, text=True, encoding='utf-8', timeout=180,
        )
        metadata = json.loads(result.stdout)
        return {'sha256': metadata['sha256'], 'source_rows': metadata['source_rows']}

    @task
    def load_raw(snapshot):
        root, python = project_paths()
        # Replay the captured content, even if the original input path has changed.
        source = root / 'data/landing' / snapshot['sha256'] / 'source.zip'
        subprocess.run(
            [str(python), '-m', 'scripts.load_bigquery', '--zip', str(source)],
            cwd=root, check=True, timeout=300,
        )
        return snapshot

    @task
    def build_models(snapshot):
        params = get_current_context()['params']
        root, python = project_paths()
        variables = {'raw_snapshot': 'lvr_sale_a_' + snapshot['sha256'],
                     'min_transaction_date': params['min_date'], 'as_of_date': params['as_of']}
        subprocess.run(
            [str(python.parent / 'dbt'), 'build',
             '--project-dir', str(root / 'analytics_bigquery'),
             '--profiles-dir', str(root / 'analytics_bigquery'),
             '--vars', json.dumps(variables)],
            cwd=root, check=True, timeout=420,
        )

    build_models(load_raw(validate_source()))
