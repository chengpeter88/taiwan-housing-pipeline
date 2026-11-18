"""scripts/seasons.py 的測試：新季別偵測、截止日規則、資料表狀態與每日排程的執行判斷。

官方網站與 BigQuery 都以假物件取代，不連網。
"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import load_bigquery as lb
from scripts import seasons as sn

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'config/seasons.json').read_text(encoding='utf-8'))
NOW = datetime(2026, 11, 18, 4, 0, tzinfo=UTC)
BASELINE = ['115S2', '115S3']


# ---------- 假物件 ----------


def fake_fetch(published: set[str], calls: list[str] | None = None):
    """模擬 load_bigquery.fetch_season：已發布回傳路徑，未發布拋 SourceValidationError。"""

    def fetch(season, landing, **kwargs):
        if calls is not None:
            calls.append(season)
        if season not in published:
            raise lb.SourceValidationError(f'{season} is not a ZIP (season not published yet?)')
        return Path(landing) / 'x' / 'source.zip'

    return fetch


class FakeClient:
    """模擬 bigquery.Client：tables 為 {table_id: expires}，last_build 為 build_metadata 的列。"""

    def __init__(self, tables: dict | None = None, last_build: dict | None = None):
        self.tables = tables or {}
        self.last_build = last_build

    def get_table(self, table_id, **kwargs):
        if table_id not in self.tables:
            raise lb.NotFound(f'{table_id} not found')
        return SimpleNamespace(expires=self.tables[table_id])

    def query(self, sql, **kwargs):
        if self.last_build is None:
            raise lb.NotFound('build_metadata not found')
        row = SimpleNamespace(**self.last_build)
        return SimpleNamespace(result=lambda **kw: [row])


def make_models_dir(tmp_path: Path) -> Path:
    models = tmp_path / 'models'
    (models / 'staging').mkdir(parents=True)
    (models / 'marts').mkdir(parents=True)
    (models / 'staging' / 'stg_lvr_sale.sql').write_text('select 1', encoding='utf-8')
    (models / 'staging' / 'schema.yml').write_text('version: 2', encoding='utf-8')
    (models / 'marts' / 'fct_housing_deal.sql').write_text('select 1', encoding='utf-8')
    (models / 'marts' / 'build_metadata.sql').write_text('select 1', encoding='utf-8')
    return models


def plan_config(**overrides) -> dict:
    config = {
        'seasons': BASELINE,
        'min_transaction_date': '2025-06-01',
        'as_of_lag_days': 160,
        'refresh_before_expiry_days': 14,
    }
    config.update(overrides)
    return config


def all_tables_fresh(tmp_path: Path, seasons: list[str]) -> dict:
    expected = sn.expected_tables(seasons, 'p', make_models_dir(tmp_path))
    return {table: NOW + timedelta(days=50) for table in expected}


# ---------- 季別與截止日 ----------


@pytest.mark.parametrize(
    'season, expected', [('115S3', '115S4'), ('115S4', '116S1'), ('099S4', '100S1')]
)
def test_next_season_rolls_over_year(season, expected):
    assert sn.next_season(season) == expected


@pytest.mark.parametrize(
    'last_season, lag, expected',
    [
        ('115S3', 160, date(2026, 3, 31)),  # 登記截止 2026-09-10，與現行設定一致
        ('115S4', 160, date(2026, 6, 30)),  # 截止 2026-12-10
        ('116S1', 160, date(2026, 9, 30)),  # 截止 2027-03-10，S1 跨年
        ('115S3', 10, date(2026, 8, 31)),  # 往前推剛好落在月底：取該日本身
        ('115S3', 11, date(2026, 7, 31)),  # 落在 8/30：取前一個月底
    ],
)
def test_as_of_is_last_month_end_before_lag(last_season, lag, expected):
    assert sn.as_of_for(last_season, lag, date(2023, 10, 1)) == expected


def test_as_of_must_not_precede_min_date():
    with pytest.raises(lb.SourceValidationError):
        sn.as_of_for('115S3', 160, date(2026, 6, 1))


def test_config_as_of_follows_rule():
    expected = sn.as_of_for(
        CONFIG['seasons'][-1],
        CONFIG['as_of_lag_days'],
        date.fromisoformat(CONFIG['min_transaction_date']),
    )
    assert CONFIG['as_of_date'] == expected.isoformat()


# ---------- 新季別偵測 ----------


def test_discover_appends_consecutive_published_seasons(tmp_path):
    calls: list[str] = []
    found = sn.discover_seasons(BASELINE, tmp_path, fetch=fake_fetch({'115S4'}, calls))
    assert found == ['115S2', '115S3', '115S4']
    assert calls == ['115S4', '116S1']  # 遇到第一個未發布就停


def test_discover_returns_baseline_when_nothing_new(tmp_path):
    assert sn.discover_seasons(BASELINE, tmp_path, fetch=fake_fetch(set())) == BASELINE


def test_discover_stops_at_max_probe(tmp_path):
    published = {'115S4', '116S1', '116S2', '116S3', '116S4'}
    found = sn.discover_seasons(BASELINE, tmp_path, fetch=fake_fetch(published), max_probe=2)
    assert found == ['115S2', '115S3', '115S4', '116S1']


def test_discover_does_not_swallow_network_errors(tmp_path):
    def broken(season, landing, **kwargs):
        raise lb.requests.ConnectionError('network down')

    with pytest.raises(lb.requests.ConnectionError):
        sn.discover_seasons(BASELINE, tmp_path, fetch=broken)


# ---------- 三層資料表 ----------


def test_expected_tables_cover_all_medallion_layers(tmp_path):
    tables = sn.expected_tables(['115S3'], 'p', make_models_dir(tmp_path))
    assert sorted(tables) == [
        'p.housing_bronze.lvr_season_a_115S3',
        'p.housing_gold.build_metadata',
        'p.housing_gold.fct_housing_deal',
        'p.housing_silver.stg_lvr_sale',
    ]


def test_table_status_reports_missing_and_expiring():
    client = FakeClient(
        tables={
            'p.d.fresh': NOW + timedelta(days=30),
            'p.d.soon': NOW + timedelta(days=5),
            'p.d.no_expiry': None,
        }
    )
    missing, expiring = sn.table_status(
        client, ['p.d.fresh', 'p.d.soon', 'p.d.no_expiry', 'p.d.gone'], NOW, refresh_days=14
    )
    assert missing == ['p.d.gone']
    assert expiring == ['p.d.soon']


# ---------- 上次成功建置的狀態 ----------


def test_read_last_build_parses_metadata_row():
    client = FakeClient(
        last_build={
            'seasons': '115S2,115S3',
            'min_transaction_date': date(2025, 6, 1),
            'as_of_date': date(2026, 3, 31),
        }
    )
    assert sn.read_last_build(client, 'p') == {
        'seasons': ['115S2', '115S3'],
        'min_transaction_date': '2025-06-01',
        'as_of_date': '2026-03-31',
    }


def test_read_last_build_missing_table_returns_none():
    assert sn.read_last_build(FakeClient(last_build=None), 'p') is None


# ---------- 執行判斷 ----------


def current_state() -> dict:
    return {
        'seasons': '115S2,115S3',
        'min_transaction_date': date(2025, 6, 1),
        'as_of_date': date(2026, 3, 31),
    }


def run_plan(tmp_path, client, published=frozenset(), **config_overrides):
    return sn.build_plan(
        plan_config(**config_overrides),
        client,
        tmp_path / 'landing',
        make_models_dir(tmp_path),
        NOW,
        project='p',
        fetch=fake_fetch(set(published)),
    )


def test_plan_skips_when_state_matches_and_tables_fresh(tmp_path):
    client = FakeClient(all_tables_fresh(tmp_path, BASELINE), last_build=current_state())
    plan = run_plan(tmp_path, client)
    assert plan['should_run'] is False
    assert plan['reasons'] == []
    assert plan['dbt_vars'] == {
        'seasons': BASELINE,
        'min_transaction_date': '2025-06-01',
        'as_of_date': '2026-03-31',
    }


def test_plan_runs_when_never_built(tmp_path):
    client = FakeClient(all_tables_fresh(tmp_path, BASELINE), last_build=None)
    plan = run_plan(tmp_path, client)
    assert plan['should_run'] is True
    assert 'no_successful_build' in plan['reasons']


def test_plan_runs_when_new_season_published(tmp_path):
    client = FakeClient(all_tables_fresh(tmp_path, BASELINE), last_build=current_state())
    plan = run_plan(tmp_path, client, published={'115S4'})
    assert plan['should_run'] is True
    assert plan['new_seasons'] == ['115S4']
    assert plan['seasons'] == ['115S2', '115S3', '115S4']
    assert plan['as_of_date'] == '2026-06-30'
    assert 'state_changed' in plan['reasons']


def test_plan_after_new_season_is_built_skips(tmp_path):
    # 新季別建置成功後，build_metadata 已是新狀態，下一次排程不應再跑
    seasons = ['115S2', '115S3', '115S4']
    built = {
        'seasons': ','.join(seasons),
        'min_transaction_date': date(2025, 6, 1),
        'as_of_date': date(2026, 6, 30),
    }
    client = FakeClient(all_tables_fresh(tmp_path, seasons), last_build=built)
    plan = run_plan(tmp_path, client, published={'115S4'})
    assert plan['should_run'] is False


def test_plan_runs_when_table_missing_or_expiring(tmp_path):
    tables = all_tables_fresh(tmp_path, BASELINE)
    tables.pop('p.housing_silver.stg_lvr_sale')
    tables['p.housing_bronze.lvr_season_a_115S2'] = NOW + timedelta(days=3)
    plan = run_plan(tmp_path, FakeClient(tables, last_build=current_state()))
    assert plan['should_run'] is True
    assert plan['missing_tables'] == ['p.housing_silver.stg_lvr_sale']
    assert plan['expiring_tables'] == ['p.housing_bronze.lvr_season_a_115S2']
    assert {'missing_tables', 'expiring_tables'} <= set(plan['reasons'])


def test_plan_refresh_days_override(tmp_path):
    # 到期門檻可由排程參數覆寫（例如 70 天 → 所有表都算快到期）
    client = FakeClient(all_tables_fresh(tmp_path, BASELINE), last_build=current_state())
    plan = run_plan(tmp_path, client, refresh_before_expiry_days=70)
    assert plan['should_run'] is True
    assert 'expiring_tables' in plan['reasons']


@pytest.mark.parametrize(
    'should_run, force, expected', [(True, False, True), (False, True, True), (False, False, False)]
)
def test_should_proceed_gate(should_run, force, expected):
    assert sn.should_proceed({'should_run': should_run}, force) is expected


# ---------- CLI ----------


def test_main_plan_prints_ascii_json(tmp_path, monkeypatch, capsys):
    client = FakeClient(all_tables_fresh(tmp_path, CONFIG['seasons']), last_build=None)
    monkeypatch.setattr(sn.bigquery, 'Client', lambda **kw: client)
    monkeypatch.setattr(sn, 'fetch_season', fake_fetch(set()))
    monkeypatch.setattr(sn, 'MODELS_DIR', make_models_dir(tmp_path))
    assert sn.main(['plan', '--landing', str(tmp_path / 'landing'), '--project', 'p']) == 0
    out = capsys.readouterr().out
    assert out.isascii()
    plan = json.loads(out)
    assert plan['should_run'] is True and plan['seasons'] == CONFIG['seasons']


def test_main_plan_validation_error_exits_3(tmp_path, monkeypatch):
    monkeypatch.setattr(sn.bigquery, 'Client', lambda **kw: FakeClient())

    def invalid_plan(*args, **kwargs):
        raise lb.SourceValidationError('as_of_date precedes min_transaction_date')

    # 規劃時的確定性錯誤（例如設定不合理）以 exit 3 結束，排程不重試
    monkeypatch.setattr(sn, 'build_plan', invalid_plan)
    assert sn.main(['plan', '--landing', str(tmp_path)]) == 3


def test_main_plan_missing_credentials_exits_3(tmp_path, monkeypatch):
    def no_credentials(**kwargs):
        raise sn.DefaultCredentialsError('no ADC')

    monkeypatch.setattr(sn.bigquery, 'Client', no_credentials)
    assert sn.main(['plan', '--landing', str(tmp_path)]) == 3
