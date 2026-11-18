"""台北實價登錄管線的 Airflow 3 DAG：每天台北中午自動判斷，需要時才全量執行。

注意：
- 每天 12:00（Asia/Taipei）先跑 plan／gate：有新季別發布、上次成功建置的狀態與期望不同、
  或任一層（bronze／silver／gold）的資料表缺少或快到期（sandbox 表 60 天到期）才往下跑，
  否則整串跳過。新季別與截止日由 scripts/seasons.py 自動推算。
- 需要執行時會連網下載官方季檔，並讀寫 BigQuery（bronze 表與 dbt 產出的 silver／gold）。
- 基準季別與日期取自 <HOUSING_PROJECT_DIR>/config/seasons.json，原始碼不寫死任何值。
- 手動觸發可用參數 force 強制全量、refresh 重新下載、refresh_before_expiry_days 調整到期門檻。
"""

import json
import logging
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from airflow.sdk import DAG, CronTriggerTimetable, Param, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException

log = logging.getLogger(__name__)

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
    required = (
        'seasons',
        'min_transaction_date',
        'as_of_date',
        'as_of_lag_days',
        'refresh_before_expiry_days',
    )
    missing = [key for key in required if config.get(key) in (None, '', [])]
    if missing:
        raise RuntimeError(f'季別設定 {path} 缺少或為空的欄位：{missing}')
    return config


def run_command(
    args: list[str],
    root: Path,
    timeout: int,
    deterministic_codes: frozenset[int] = DETERMINISTIC_EXIT_CODES,
) -> str:
    """執行子程序並把輸出寫入 task log，回傳 stdout。

    exit code 屬於 deterministic_codes 時拋 AirflowFailException（不重試）；
    其他非 0 或逾時則拋 RuntimeError，交由 Airflow 重試。
    """
    log.info('執行指令：%s', ' '.join(args))
    try:
        result = subprocess.run(
            args,
            cwd=root,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=timeout,
            check=False,
        )
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
    dag_id='taipei_housing_pipeline',
    start_date=datetime(2026, 11, 16, tzinfo=ZoneInfo('Asia/Taipei')),
    schedule=CronTriggerTimetable('0 12 * * *', timezone='Asia/Taipei'),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=1,
    dagrun_timeout=timedelta(minutes=90),
    default_args={
        'retries': 2,
        'retry_delay': timedelta(minutes=1),
        'execution_timeout': timedelta(minutes=10),
    },
    params={
        'force': Param(False, type='boolean'),
        'refresh': Param(False, type='boolean'),
        'refresh_before_expiry_days': Param(
            _CONFIG['refresh_before_expiry_days'], type='integer', minimum=1
        ),
    },
    tags=['housing', 'bigquery', 'scheduled'],
) as dag:

    @task(execution_timeout=timedelta(minutes=10))
    def plan() -> dict:
        """偵測新季別並比對 BigQuery 狀態，回傳執行計畫（scripts.seasons plan 的 JSON）。"""
        params = get_current_context()['params']
        stdout = run_command(
            [
                str(_PYTHON),
                '-m',
                'scripts.seasons',
                'plan',
                '--refresh-days',
                str(params['refresh_before_expiry_days']),
            ],
            _ROOT,
            timeout=480,
        )
        try:
            result = json.loads(stdout)
            result['seasons'], result['should_run'], result['dbt_vars']
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise AirflowFailException(f'規劃輸出不符合契約：{exc}') from exc
        return result

    @task.short_circuit
    def gate(plan_output: dict) -> bool:
        """需要執行（或手動強制）才放行；否則下游全部 skipped。"""
        params = get_current_context()['params']
        proceed = bool(plan_output['should_run'] or params['force'])
        log.info(
            '執行判斷：proceed=%s force=%s reasons=%s new_seasons=%s',
            proceed,
            params['force'],
            plan_output['reasons'],
            plan_output['new_seasons'],
        )
        return proceed

    @task
    def season_list(plan_output: dict) -> list[str]:
        """取出計畫的季別清單（dynamic task mapping 不能直接對 dict 的鍵展開）。"""
        return list(plan_output['seasons'])

    @task(execution_timeout=timedelta(minutes=5))
    def fetch_validate(season: str) -> dict:
        """下載並驗證單一季別，回傳 {season, sha256, source_rows}。"""
        params = get_current_context()['params']
        args = [str(_PYTHON), '-m', 'scripts.load_bigquery', '--season', season, '--validate-only']
        if params['refresh']:
            args.append('--refresh')
        stdout = run_command(args, _ROOT, timeout=180)
        try:
            metadata = json.loads(stdout)
            return {
                'season': season,
                'sha256': metadata['sha256'],
                'source_rows': metadata['source_rows'],
            }
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise AirflowFailException(f'載入器輸出不符合契約（{season}）：{exc}') from exc

    @task(execution_timeout=timedelta(minutes=8))
    def load_season(snapshot: dict) -> dict:
        """以驗證過的同一份檔案把單一季別載入 BigQuery（載入器會先刪表以重設到期日）。"""
        source = _ROOT / 'data' / 'landing' / snapshot['sha256'] / 'source.zip'
        if not source.is_file():
            raise AirflowFailException(f'找不到已驗證的來源檔：{source}')
        run_command(
            [
                str(_PYTHON),
                '-m',
                'scripts.load_bigquery',
                '--season',
                snapshot['season'],
                '--zip',
                str(source),
            ],
            _ROOT,
            timeout=300,
        )
        return snapshot

    # dbt 測試失敗是資料或邏輯問題，重跑也不會過，只會再消耗 BigQuery 查詢額度，所以不重試。
    @task(execution_timeout=timedelta(minutes=25), retries=0)
    def build_models(plan_output: dict, loaded: list[dict]) -> None:
        """全部季別載入完成後，以計畫的 dbt_vars 執行 dbt build。"""
        variables = plan_output['dbt_vars']
        expected = sorted(variables['seasons'])
        loaded_seasons = sorted(item['season'] for item in loaded)
        if loaded_seasons != expected:
            raise AirflowFailException(f'已載入季別 {loaded_seasons} 與預期 {expected} 不一致')
        project_dir = str(_ROOT / 'analytics_bigquery')
        run_command(
            [
                str(_PYTHON.parent / 'dbt'),
                'build',
                '--project-dir',
                project_dir,
                '--profiles-dir',
                project_dir,
                '--vars',
                json.dumps(variables),
            ],
            _ROOT,
            timeout=900,
            deterministic_codes=frozenset(),
        )

    plan_output = plan()
    gate_result = gate(plan_output)
    seasons = season_list(plan_output)
    gate_result >> seasons
    snapshots = fetch_validate.expand(season=seasons)
    loaded_snapshots = load_season.expand(snapshot=snapshots)
    build_models(plan_output, loaded_snapshots)
