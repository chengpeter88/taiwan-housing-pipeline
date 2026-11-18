"""scripts.verify_bigquery 的測試：BigQuery 以假 client 取代，不連網。"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import verify_bigquery as vb
from scripts.load_bigquery import NotFound

NOW = datetime(2026, 11, 18, tzinfo=UTC)
PROJECT = 'p'
MIB = 2**20


def make_table(rows=10, mib=1.0, days_left=50, table_type='TABLE'):
    return SimpleNamespace(
        table_type=table_type,
        num_rows=rows,
        num_bytes=int(mib * MIB),
        expires=None if days_left is None else NOW + timedelta(days=days_left),
    )


class FakeClient:
    def __init__(self, tables=None, bronze_rows=None, build_row=None):
        self.tables = tables or {}
        self.bronze_rows = bronze_rows or []
        self.build_row = build_row
        self.queries = []

    def get_table(self, table_id, **kwargs):
        if table_id not in self.tables:
            raise NotFound(table_id)
        return self.tables[table_id]

    def query(self, sql, **kwargs):
        self.queries.append(sql)
        if 'lvr_season_a_*' in sql:
            rows = self.bronze_rows
        elif 'build_metadata' in sql:
            if self.build_row is None:
                raise NotFound('build_metadata')
            rows = [self.build_row]
        else:
            raise AssertionError(sql)
        return SimpleNamespace(result=lambda **kw: rows)


def write_manifest(landing: Path, season: str, sha: str, rows: int) -> None:
    (landing / 'seasons').mkdir(parents=True, exist_ok=True)
    (landing / 'seasons' / f'{season}.json').write_text(
        json.dumps({'season': season, 'sha256': sha}), encoding='utf-8'
    )
    (landing / sha).mkdir(parents=True, exist_ok=True)
    (landing / sha / 'metadata.json').write_text(
        json.dumps({'sha256': sha, 'source_rows': rows}), encoding='utf-8'
    )


def config(seasons=('115S2', '115S3')):
    return {
        'seasons': list(seasons),
        'min_transaction_date': '2023-10-01',
        'as_of_date': '2026-03-31',
        'refresh_before_expiry_days': 14,
    }


def healthy_inputs():
    """一組完全正常的輸入，供各測試只改一處。"""
    tables = [
        {
            'table_id': 'p.housing_bronze.lvr_season_a_115S2',
            'exists': True,
            'table_type': 'TABLE',
            'num_rows': 5,
            'mib': 1.0,
            'days_left': 50,
        },
    ]
    bronze = {
        '115S2': {'rows': 5, 'sha256s': ['a']},
        '115S3': {'rows': 7, 'sha256s': ['b']},
    }
    manifest = {
        '115S2': {'sha256': 'a', 'source_rows': 5},
        '115S3': {'sha256': 'b', 'source_rows': 7},
    }
    last_build = {
        'seasons': ['115S2', '115S3'],
        'min_transaction_date': '2023-10-01',
        'as_of_date': '2026-03-31',
    }
    return tables, bronze, manifest, last_build


# ---------- inspect_tables ----------


def test_inspect_tables_reports_rows_size_and_days_left():
    client = FakeClient(tables={'p.d.t': make_table(rows=3, mib=2.5, days_left=40)})
    [info] = vb.inspect_tables(client, ['p.d.t'], NOW)
    assert info == {
        'table_id': 'p.d.t',
        'exists': True,
        'table_type': 'TABLE',
        'num_rows': 3,
        'mib': 2.5,
        'days_left': 40,
    }


def test_inspect_tables_marks_missing_table():
    [info] = vb.inspect_tables(FakeClient(), ['p.d.gone'], NOW)
    assert info['exists'] is False
    assert info['num_rows'] is None and info['days_left'] is None


def test_inspect_tables_handles_no_expiration():
    client = FakeClient(tables={'p.d.t': make_table(days_left=None)})
    [info] = vb.inspect_tables(client, ['p.d.t'], NOW)
    assert info['days_left'] is None


# ---------- bronze_stats / source_manifest ----------


def test_bronze_stats_groups_by_season_with_sorted_sha256():
    rows = [
        SimpleNamespace(season='115S3', row_count=7, sha256s=['b']),
        SimpleNamespace(season='115S2', row_count=5, sha256s=['z', 'a']),
    ]
    client = FakeClient(bronze_rows=rows)
    stats = vb.bronze_stats(client, PROJECT)
    assert stats == {
        '115S2': {'rows': 5, 'sha256s': ['a', 'z']},
        '115S3': {'rows': 7, 'sha256s': ['b']},
    }
    assert '`p.housing_bronze.lvr_season_a_*`' in client.queries[0]


def test_bronze_stats_returns_empty_when_dataset_missing():
    class NoBronze(FakeClient):
        def query(self, sql, **kwargs):
            raise NotFound('housing_bronze')

    assert vb.bronze_stats(NoBronze(), PROJECT) == {}


def test_source_manifest_reads_landing_cache(tmp_path):
    write_manifest(tmp_path, '115S3', 'b', 7)
    manifest = vb.source_manifest(['115S3', '115S4'], tmp_path)
    assert manifest == {'115S3': {'sha256': 'b', 'source_rows': 7}, '115S4': None}


# ---------- find_problems ----------


def test_find_problems_healthy_state_has_no_problems_or_notes():
    tables, bronze, manifest, last_build = healthy_inputs()
    problems, notes = vb.find_problems(config(), tables, bronze, manifest, last_build)
    assert problems == [] and notes == []


def test_find_problems_flags_missing_table():
    tables, bronze, manifest, last_build = healthy_inputs()
    tables[0] = {**tables[0], 'exists': False}
    problems, _ = vb.find_problems(config(), tables, bronze, manifest, last_build)
    assert any('lvr_season_a_115S2' in p for p in problems)


def test_find_problems_flags_bronze_row_count_mismatch():
    tables, bronze, manifest, last_build = healthy_inputs()
    bronze['115S3'] = {'rows': 6, 'sha256s': ['b']}
    problems, _ = vb.find_problems(config(), tables, bronze, manifest, last_build)
    assert any('115S3' in p and '6' in p and '7' in p for p in problems)


def test_find_problems_flags_sha256_mismatch():
    tables, bronze, manifest, last_build = healthy_inputs()
    bronze['115S3'] = {'rows': 7, 'sha256s': ['other']}
    problems, _ = vb.find_problems(config(), tables, bronze, manifest, last_build)
    assert any('115S3' in p and 'sha256' in p for p in problems)


def test_find_problems_flags_configured_season_absent_from_bronze():
    tables, bronze, manifest, last_build = healthy_inputs()
    del bronze['115S3']
    problems, _ = vb.find_problems(config(), tables, bronze, manifest, last_build)
    assert any('115S3' in p for p in problems)


def test_find_problems_notes_when_source_cache_missing():
    """沒有本機下載快取時無法對官方筆數，只提醒、不算失敗。"""
    tables, bronze, manifest, last_build = healthy_inputs()
    manifest['115S3'] = None
    problems, notes = vb.find_problems(config(), tables, bronze, manifest, last_build)
    assert problems == []
    assert any('115S3' in n for n in notes)


def test_find_problems_flags_missing_build_metadata():
    tables, bronze, manifest, _ = healthy_inputs()
    problems, _ = vb.find_problems(config(), tables, bronze, manifest, None)
    assert any('build_metadata' in p for p in problems)


def test_find_problems_flags_build_metadata_mismatch():
    tables, bronze, manifest, last_build = healthy_inputs()
    last_build['as_of_date'] = '2025-12-31'
    problems, _ = vb.find_problems(config(), tables, bronze, manifest, last_build)
    assert any('as_of_date' in p for p in problems)


def test_find_problems_notes_bigquery_newer_than_repo():
    """自動納入新季別後 BigQuery 會比 repo 新：這是預期狀態，只提醒同步 config。"""
    tables, bronze, manifest, last_build = healthy_inputs()
    last_build['seasons'] = ['115S2', '115S3', '115S4']
    last_build['as_of_date'] = '2026-06-30'
    bronze['115S4'] = {'rows': 9, 'sha256s': ['c']}
    problems, notes = vb.find_problems(config(), tables, bronze, manifest, last_build)
    assert problems == []
    assert any('115S4' in n and 'config/seasons.json' in n for n in notes)


def test_find_problems_notes_tables_expiring_soon():
    tables, bronze, manifest, last_build = healthy_inputs()
    tables[0] = {**tables[0], 'days_left': 3}
    problems, notes = vb.find_problems(config(), tables, bronze, manifest, last_build)
    assert problems == []
    assert any('lvr_season_a_115S2' in n and '3' in n for n in notes)


# ---------- storage_summary ----------


def test_storage_summary_counts_only_tables_and_estimates_runs():
    tables = [
        {'exists': True, 'table_type': 'TABLE', 'mib': 60.0},
        {'exists': True, 'table_type': 'TABLE', 'mib': 40.0},
        {'exists': True, 'table_type': 'VIEW', 'mib': 0.0},
        {'exists': False, 'table_type': None, 'mib': None},
    ]
    summary = vb.storage_summary(tables)
    assert summary == {'mib_per_full_run': 100.0, 'full_runs_per_lifetime_quota': 102}


def test_storage_summary_with_no_storage():
    summary = vb.storage_summary([])
    assert summary == {'mib_per_full_run': 0.0, 'full_runs_per_lifetime_quota': None}


# ---------- main ----------


@pytest.fixture
def healthy_world(tmp_path, monkeypatch):
    """main 用的完整假環境：config、models 目錄、landing 快取與 BigQuery。"""
    cfg = config()
    cfg_path = tmp_path / 'seasons.json'
    cfg_path.write_text(json.dumps(cfg), encoding='utf-8')
    models = tmp_path / 'models'
    (models / 'staging').mkdir(parents=True)
    (models / 'marts').mkdir(parents=True)
    (models / 'staging' / 'stg_lvr_sale.sql').write_text('select 1', encoding='utf-8')
    (models / 'marts' / 'build_metadata.sql').write_text('select 1', encoding='utf-8')
    landing = tmp_path / 'landing'
    write_manifest(landing, '115S2', 'a', 5)
    write_manifest(landing, '115S3', 'b', 7)
    tables = {
        'p.housing_bronze.lvr_season_a_115S2': make_table(rows=5),
        'p.housing_bronze.lvr_season_a_115S3': make_table(rows=7),
        'p.housing_silver.stg_lvr_sale': make_table(rows=12),
        'p.housing_gold.build_metadata': make_table(rows=1),
    }
    build_row = SimpleNamespace(
        seasons='115S2,115S3',
        min_transaction_date=datetime(2023, 10, 1).date(),
        as_of_date=datetime(2026, 3, 31).date(),
    )
    bronze_rows = [
        SimpleNamespace(season='115S2', row_count=5, sha256s=['a']),
        SimpleNamespace(season='115S3', row_count=7, sha256s=['b']),
    ]
    client = FakeClient(tables=tables, bronze_rows=bronze_rows, build_row=build_row)
    monkeypatch.setattr(vb, 'CONFIG_PATH', cfg_path)
    monkeypatch.setattr(vb, 'MODELS_DIR', models)
    monkeypatch.setattr(vb.bigquery, 'Client', lambda project: client)
    return SimpleNamespace(landing=landing, client=client)


def test_main_returns_zero_and_json_report_when_healthy(healthy_world, capsys):
    code = vb.main(['--project', PROJECT, '--landing', str(healthy_world.landing), '--json'])
    out = capsys.readouterr().out
    assert code == 0
    assert out.isascii()
    report = json.loads(out)
    assert report['problems'] == []
    assert len(report['tables']) == 4
    assert report['storage']['mib_per_full_run'] == 4.0


def test_main_returns_one_when_problem_found(healthy_world, capsys):
    del healthy_world.client.tables['p.housing_silver.stg_lvr_sale']
    code = vb.main(['--project', PROJECT, '--landing', str(healthy_world.landing)])
    out = capsys.readouterr().out
    assert code == 1
    assert 'stg_lvr_sale' in out


def test_main_returns_three_without_credentials(healthy_world, monkeypatch):
    def no_credentials(project):
        raise vb.DefaultCredentialsError('no ADC')

    monkeypatch.setattr(vb.bigquery, 'Client', no_credentials)
    assert vb.main(['--project', PROJECT, '--landing', str(healthy_world.landing)]) == 3
