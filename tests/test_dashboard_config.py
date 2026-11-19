"""儀表板（Evidence 40.1.8 ＋ GitHub Pages）的設定契約。

公開 repo 加上公開網站：權限、觸發條件、公開的資料範圍與統計口徑都要有測試守著。
"""

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / 'dashboard'
WORKFLOW = '.github/workflows/dashboard.yml'
REPO_NAME = 'taiwan-housing-pipeline'
BUILD_DIR = f'./build/{REPO_NAME}'
GOLD_MODELS = ROOT / 'analytics_bigquery/models/marts'
# 公開網站只放彙總表與建置狀態；逐筆交易（fct）與 silver／bronze 不公開
ALLOWED_SOURCE_TABLES = {
    'agg_city_month_market',
    'agg_district_quarter_price',
    'agg_district_segment_price',
    'agg_district_budget',
    'build_metadata',
}
LOW_SAMPLE_TABLES = {
    'agg_district_quarter_price',
    'agg_district_segment_price',
    'agg_district_budget',
}
FORBIDDEN_ROLES = ['roles/editor', 'roles/owner', 'roles/bigquery.admin', 'roles/bigquery.user']


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def _workflow() -> dict:
    return _yaml(ROOT / WORKFLOW)


def _triggers(workflow: dict) -> dict:
    # PyYAML 把 `on` 讀成布林 True
    return workflow.get('on', workflow.get(True))


def _step_index(steps: list[dict], needle: str) -> int:
    for index, step in enumerate(steps):
        if needle in json.dumps(step, ensure_ascii=False):
            return index
    raise AssertionError(f'step containing {needle!r} not found')


def _source_files() -> dict[str, str]:
    return {
        path.stem: path.read_text(encoding='utf-8')
        for path in sorted((DASHBOARD / 'sources/housing').glob('*.sql'))
    }


def _pages() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding='utf-8')
        for path in sorted((DASHBOARD / 'pages').glob('*.md'))
    }


# ---------- workflow ----------


def test_dashboard_workflow_triggers_exclude_pull_requests():
    triggers = _triggers(_workflow())
    # 公開 repo：任何人都能開 PR，PR 不得觸發讀正式資料的 workflow
    assert set(triggers) == {'push', 'schedule', 'workflow_dispatch'}
    assert triggers['push']['branches'] == ['main']
    assert set(triggers['push']['paths']) == {'dashboard/**', WORKFLOW}
    # 台北 14:30，在 Airflow（台北 12:00）之後
    assert triggers['schedule'] == [{'cron': '30 6 * * *'}]


def test_dashboard_workflow_permissions_are_scoped_per_job():
    workflow = _workflow()
    assert workflow['permissions'] == {'contents': 'read'}
    assert workflow['concurrency'] == {'group': 'pages', 'cancel-in-progress': False}
    jobs = workflow['jobs']
    assert set(jobs) == {'build', 'deploy'}
    assert jobs['build']['permissions'] == {'contents': 'read', 'id-token': 'write'}
    assert jobs['deploy']['permissions'] == {'pages': 'write', 'id-token': 'write'}
    assert jobs['deploy']['needs'] == 'build'
    assert jobs['deploy']['environment']['name'] == 'github-pages'
    for job in jobs.values():
        assert 0 < job['timeout-minutes'] <= 30


def test_dashboard_auth_uses_wif_variables_without_keys():
    steps = _workflow()['jobs']['build']['steps']
    auth = steps[_step_index(steps, 'google-github-actions/auth@v3')]
    assert auth['with'] == {
        'workload_identity_provider': '${{ vars.GCP_WIF_PROVIDER }}',
        'service_account': '${{ vars.GCP_DASHBOARD_SERVICE_ACCOUNT }}',
    }
    # checkout 必須在 auth 之前，否則 auth 產生的憑證檔會被清掉
    assert _step_index(steps, 'actions/checkout') < _step_index(steps, 'google-github-actions/auth')
    text = (ROOT / WORKFLOW).read_text(encoding='utf-8')
    for secret_hint in ['credentials_json', 'keyfile', 'private_key', 'secrets.']:
        assert secret_hint not in text


def test_dashboard_checks_variables_before_auth():
    steps = _workflow()['jobs']['build']['steps']
    check = _step_index(steps, 'GCP_DASHBOARD_SERVICE_ACCOUNT')
    assert check < _step_index(steps, 'google-github-actions/auth')
    assert 'exit 1' in steps[check]['run']


def test_dashboard_uses_npm_ci_and_strict_builds():
    build = _workflow()['jobs']['build']
    steps = build['steps']
    runs = [step.get('run', '') for step in steps]
    assert 'npm ci' in runs
    assert not any('npm install' in run for run in runs)
    # 非 strict 模式在查詢失敗時仍可能產出網站，必須用 strict 才會讓 deploy 不執行
    sources, site = _step_index(steps, 'npm run sources:strict'), _step_index(
        steps, 'npm run build:strict'
    )
    assert _step_index(steps, 'google-github-actions/auth') < sources < site
    # 實測：40.1.8 的 build:strict 在頁面查詢錯誤時只印 "Error in Query" 仍 exit 0，
    # 所以 workflow 要自己檢查 log；shell: bash 才有 pipefail，tee 不會吃掉 exit code
    site_step = steps[site]
    assert site_step['shell'] == 'bash'
    assert 'tee' in site_step['run'] and 'Error in Query' in site_step['run']
    assert 'exit 1' in site_step['run']
    node = steps[_step_index(steps, 'actions/setup-node')]['with']
    assert re.fullmatch(r'\d+\.\d+\.\d+', str(node['node-version']))
    assert node['cache-dependency-path'] == 'dashboard/package-lock.json'
    assert build['defaults']['run']['working-directory'] == 'dashboard'
    assert build['env']['SEND_ANONYMOUS_USAGE_STATS'] == 'no'


def test_dashboard_artifact_path_matches_base_path():
    build = _workflow()['jobs']['build']
    assert build['env']['EVIDENCE_BUILD_DIR'] == BUILD_DIR
    steps = build['steps']
    upload = steps[_step_index(steps, 'actions/upload-pages-artifact')]
    assert upload['with']['path'] == f'dashboard/build/{REPO_NAME}'
    deploy_steps = _workflow()['jobs']['deploy']['steps']
    assert any(step.get('uses', '').startswith('actions/deploy-pages@') for step in deploy_steps)


# ---------- Evidence 專案 ----------


def test_evidence_base_path_matches_repository_name():
    config = _yaml(DASHBOARD / 'evidence.config.yaml')
    assert config['deployment']['basePath'] == f'/{REPO_NAME}'
    # 只裝實際用到的資料來源 plugin
    assert set(config['plugins']['datasources']) == {'@evidence-dev/bigquery'}


def test_evidence_connection_has_no_key_fields():
    connection = _yaml(DASHBOARD / 'sources/housing/connection.yaml')
    assert connection['type'] == 'bigquery'
    options = connection['options']
    # gcloud-cli 交給 ADC：本機用 gcloud 登入，CI 用 WIF 產生的短期憑證檔
    assert options == {
        'project_id': 'taiwan-household',
        'location': 'US',
        'authenticator': 'gcloud-cli',
    }


def test_dashboard_package_versions_are_exact():
    package = json.loads((DASHBOARD / 'package.json').read_text(encoding='utf-8'))
    dependencies = package['dependencies']
    assert dependencies == {
        '@evidence-dev/bigquery': '2.0.12',
        '@evidence-dev/core-components': '5.4.2',
        '@evidence-dev/evidence': '40.1.8',
    }
    for version in dependencies.values():
        assert re.fullmatch(r'\d+\.\d+\.\d+', version)
    scripts = package['scripts']
    assert scripts['sources:strict'] == 'evidence sources --strict'
    assert scripts['build:strict'] == 'evidence build:strict'
    # 新版 template 把 scripts 換成 echo，執行後 exit 0 卻什麼都沒建
    assert not any('echo' in command for command in scripts.values())
    lock = json.loads((DASHBOARD / 'package-lock.json').read_text(encoding='utf-8'))
    assert lock['packages']['node_modules/@evidence-dev/evidence']['version'] == '40.1.8'


def test_gitignore_excludes_evidence_artifacts():
    ignored = (ROOT / '.gitignore').read_text(encoding='utf-8').splitlines()
    for entry in [
        'dashboard/node_modules/',
        'dashboard/build/',
        'dashboard/.evidence/template/',
        'dashboard/.svelte-kit/',
        'dashboard/static/data/',
        '*.options.yaml',
    ]:
        assert entry in ignored


# ---------- 公開的資料範圍 ----------


def _source_columns(sql: str) -> tuple[list[str], str]:
    match = re.fullmatch(
        r'(?:--[^\n]*\n)*select\n(?P<cols>(?: {4}\w+,?\n)+)from `taiwan-household\.housing_gold\.(?P<table>\w+)`\n?',
        sql,
    )
    assert match, 'source SQL 必須是：註解、select、每行一個欄位、from 一張 gold 表'
    columns = [line.strip().rstrip(',') for line in match['cols'].splitlines()]
    return columns, match['table']


def test_evidence_source_queries_only_read_gold_aggregates():
    sources = _source_files()
    assert set(sources) == {
        'city_month_market',
        'district_quarter_price',
        'district_segment_price',
        'district_budget',
        'build_metadata',
    }
    tables = set()
    for sql in sources.values():
        columns, table = _source_columns(sql)
        assert columns, 'select * 不允許：明確列出公開的欄位'
        tables.add(table)
    assert tables == ALLOWED_SOURCE_TABLES


def test_evidence_source_columns_exist_in_gold_models():
    # mart 改欄位時，pytest 先擋下，不必等隔天排程才發現網站壞掉
    for sql in _source_files().values():
        columns, table = _source_columns(sql)
        model = (GOLD_MODELS / f'{table}.sql').read_text(encoding='utf-8')
        for column in columns:
            assert re.search(rf'\b{column}\b', model), f'{table} 沒有欄位 {column}'


# ---------- 頁面與統計口徑 ----------


def test_dashboard_has_expected_pages():
    assert set(_pages()) == {'index.md', 'market.md', 'districts.md', 'segments.md', 'budget.md'}


def test_pages_never_aggregate_medians_or_percentiles():
    # 專案規則：中位數／分位數不可再平均或加總；跨期、跨區要從逐筆交易重新計算
    pattern = re.compile(r'\b(avg|sum|mean)\s*\(\s*\w*(median|p25|p75)', re.IGNORECASE)
    for name, page in _pages().items():
        assert not pattern.search(page), name
        assert 'totalRow' not in page, name
        # 長條圖預設會把多個 series 疊加，中位數不能疊
        for chart in re.findall(r'<BarChart[^>]*>', page, flags=re.DOTALL):
            if 'median' in chart:
                assert 'type=grouped' in chart, name


def test_pages_with_low_sample_tables_show_low_sample_flag():
    query_table = {
        'city_month_market': 'agg_city_month_market',
        'district_quarter_price': 'agg_district_quarter_price',
        'district_segment_price': 'agg_district_segment_price',
        'district_budget': 'agg_district_budget',
    }
    for name, page in _pages().items():
        used = {table for query, table in query_table.items() if f'housing.{query}' in page}
        if used & LOW_SAMPLE_TABLES:
            assert 'is_low_sample' in page, name


def test_index_page_shows_freshness_and_source_attribution():
    index = _pages()['index.md']
    for needle in ['housing.build_metadata', 'as_of_date', 'built_at', 'seasons']:
        assert needle in index
    assert 'data.gov.tw/license' in index


# ---------- GCP 權限 ----------


def test_setup_wif_dashboard_account_reads_gold_from_main_only():
    script = (ROOT / 'infra/setup_wif.sh').read_text(encoding='utf-8')
    assert 'DASHBOARD_SA_NAME="dashboard-reader"' in script
    # 只有 main 分支上的 workflow 能換到 dashboard 帳號
    assert "attribute.repo_ref=assertion.repository_id + ':' + assertion.ref" in script
    assert script.count('--attribute-mapping="${ATTRIBUTE_MAPPING}"') == 2
    assert 'attribute.repo_ref/${REPO_ID}:refs/heads/main' in script
    assert 'grant_dataset "${DASHBOARD_SA_EMAIL}" housing_gold READER' in script
    dashboard_grants = re.findall(r'grant_dataset "\$\{DASHBOARD_SA_EMAIL\}" .+', script)
    assert dashboard_grants == ['grant_dataset "${DASHBOARD_SA_EMAIL}" housing_gold READER']
    for role in FORBIDDEN_ROLES:
        assert role not in script
    assert 'GCP_DASHBOARD_SERVICE_ACCOUNT=' in script
