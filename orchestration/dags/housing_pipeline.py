"""台北實價登錄管線的 Airflow 3 DAG：多季逐季下載、驗證、載入，再執行 dbt。

注意：
- 只接受手動觸發（schedule=None），不會自動排程。
- 執行時會實際連網下載官方季檔，並讀寫 BigQuery（raw 表與 dbt 產出的 staging／marts）。
- 預設季別與日期取自 <HOUSING_PROJECT_DIR>/config/seasons.json，原始碼不寫死任何值。
"""
import json
import logging
import os
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from airflow.sdk import DAG, Param, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException

log = logging.getLogger(__name__)

SEASON_PATTERN = re.compile(r'^\d{3}S[1-4]$')
# 載入器約定：2 為參數錯誤、3 為確定性驗證錯誤，重試也不會成功。
DETERMINISTIC_EXIT_CODES = frozenset({2, 3})


def project_paths() -> tuple[Path, Path]:
    """回傳（專案根目錄, 專案虛擬環境的 python 路徑）。"""
    root = Path(os.environ.get('HOUSING_PROJECT_DIR', '/opt/housing'))
    python = Path(os.environ.get('HOUSING_PYTHON', '/opt/housing-venv/bin/python'))
    return root, python


def load_config(root: Path) -> dict:
    """讀取 config/seasons.json；檔案缺漏、格式錯誤或欄位不全時明確報錯。"""
    path = root / 'config' / 'seasons.json'
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f'無法讀取季別設定 {path}：{exc}') from exc
    missing = [key for key in ('seasons', 'min_transaction_date', 'as_of_date')
               if not config.get(key)]
    if missing:
        raise RuntimeError(f'季別設定 {path} 缺少或為空的欄位：{missing}')
    return config


def run_command(args: list[str], root: Path, timeout: int,
                deterministic_codes: frozenset[int] = DETERMINISTIC_EXIT_CODES) -> str:
    """執行子程序並把輸出寫入 task log，回傳 stdout。

    exit code 屬於 deterministic_codes 時拋 AirflowFailException（不重試）；
    其他非 0 或逾時則拋 RuntimeError，交由 Airflow 重試。
    """
    log.info('執行指令：%s', ' '.join(args))
    try:
        result = subprocess.run(args, cwd=root, capture_output=True, text=True,
                                encoding='utf-8', timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f'指令逾時（{timeout} 秒）：{" ".join(args)}') from exc
    if result.stdout:
        log.info('stdout:\n%s', result.stdout)
    if result.stderr:
        log.info('stderr:\n%s', result.stderr)
    if result.returncode in deterministic_codes:
        raise AirflowFailException(f'指令以確定性錯誤結束（exit {result.returncode}），不重試')
    if result.returncode != 0:
        raise RuntimeError(f'指令失敗（exit {result.returncode}），將依設定重試')
    return result.stdout


_ROOT, _PYTHON = project_paths()
_CONFIG = load_config(_ROOT)

with DAG(
    dag_id='taipei_housing_manual',
    start_date=datetime(2026, 11, 16, tzinfo=UTC),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    max_active_tasks=1,
    dagrun_timeout=timedelta(minutes=90),
    default_args={'retries': 2, 'retry_delay': timedelta(minutes=1),
                  'execution_timeout': timedelta(minutes=10)},
    params={
        'seasons': Param(list(_CONFIG['seasons']), type='array', items={'type': 'string'},
                         minItems=1),
        'min_date': Param(_CONFIG['min_transaction_date'], type='string', format='date'),
        'as_of': Param(_CONFIG['as_of_date'], type='string', format='date'),
        'refresh': Param(False, type='boolean'),
    },
    tags=['housing', 'bigquery', 'manual'],
) as dag:

    @task
    def resolve_seasons() -> list[str]:
        """驗證 params 並回傳去重、排序後的季別清單。"""
        params = get_current_context()['params']
        seasons = params['seasons']
        invalid = [s for s in seasons if not isinstance(s, str) or not SEASON_PATTERN.match(s)]
        if invalid:
            raise AirflowFailException(f'季別格式不合法：{invalid}')
        resolved = sorted(set(seasons))
        if not resolved:
            raise AirflowFailException('季別清單不可為空')
        if params['min_date'] > params['as_of']:
            raise AirflowFailException('min_date 不可晚於 as_of')
        log.info('本次處理季別：%s', resolved)
        return resolved

    @task(execution_timeout=timedelta(minutes=5))
    def fetch_validate(season: str) -> dict:
        """下載並驗證單一季別，回傳 {season, sha256, source_rows}。"""
        params = get_current_context()['params']
        args = [str(_PYTHON), '-m', 'scripts.load_bigquery', '--season', season,
                '--validate-only']
        if params['refresh']:
            args.append('--refresh')
        stdout = run_command(args, _ROOT, timeout=180)
        try:
            metadata = json.loads(stdout)
            return {'season': season, 'sha256': metadata['sha256'],
                    'source_rows': metadata['source_rows']}
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise AirflowFailException(f'載入器輸出不符合契約（{season}）：{exc}') from exc

    @task(execution_timeout=timedelta(minutes=8))
    def load_season(snapshot: dict) -> dict:
        """以驗證過的同一份檔案把單一季別載入 BigQuery。"""
        source = _ROOT / 'data' / 'landing' / snapshot['sha256'] / 'source.zip'
        if not source.is_file():
            raise AirflowFailException(f'找不到已驗證的來源檔：{source}')
        run_command([str(_PYTHON), '-m', 'scripts.load_bigquery',
                     '--season', snapshot['season'], '--zip', str(source)],
                    _ROOT, timeout=300)
        return snapshot

    # dbt 測試失敗是資料或邏輯問題，重跑也不會過，只會再消耗 BigQuery 查詢額度，所以不重試。
    @task(execution_timeout=timedelta(minutes=25), retries=0)
    def build_models(seasons: list[str], loaded: list[dict]) -> None:
        """全部季別載入完成後執行 dbt build。"""
        params = get_current_context()['params']
        loaded_seasons = sorted(item['season'] for item in loaded)
        if loaded_seasons != list(seasons):
            raise AirflowFailException(
                f'已載入季別 {loaded_seasons} 與預期 {list(seasons)} 不一致')
        variables = {'seasons': list(seasons), 'min_transaction_date': params['min_date'],
                     'as_of_date': params['as_of']}
        project_dir = str(_ROOT / 'analytics_bigquery')
        run_command([str(_PYTHON.parent / 'dbt'), 'build',
                     '--project-dir', project_dir, '--profiles-dir', project_dir,
                     '--vars', json.dumps(variables)],
                    _ROOT, timeout=900, deterministic_codes=frozenset())

    season_list = resolve_seasons()
    snapshots = fetch_validate.expand(season=season_list)
    loaded_snapshots = load_season.expand(snapshot=snapshots)
    build_models(season_list, loaded_snapshots)
