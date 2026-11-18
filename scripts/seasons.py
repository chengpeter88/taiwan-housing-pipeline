"""每日排程的判斷邏輯：偵測新季別、推算截止日，並比對 BigQuery 狀態決定要不要全量執行。

Invoke from the project root with: python -m scripts.seasons plan
stdout 只輸出一份 ASCII JSON 計畫（給 Airflow 解析）；log 一律走 stderr。
"""

import argparse
import json
import logging
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from google.auth.exceptions import DefaultCredentialsError
from google.cloud import bigquery

from scripts.load_bigquery import (
    NotFound,
    SourceValidationError,
    fetch_season,
    parse_season,
    registration_period,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / 'config/seasons.json'
MODELS_DIR = ROOT / 'analytics_bigquery/models'
LOGGER = logging.getLogger(__name__)


def next_season(season: str) -> str:
    """回傳下一個季別；S4 之後進入下一年的 S1，年份補零到 3 碼。

    Args:
        season: 季別，例如 115S4。

    Returns:
        下一季，例如 116S1。

    Raises:
        SourceValidationError: 季別格式錯誤。
    """
    year, quarter = parse_season(season)
    if quarter == 4:
        return f'{year + 1:03d}S1'
    return f'{year:03d}S{quarter + 1}'


def as_of_for(last_season: str, lag_days: int, min_date: date) -> date:
    """推算分析截止日：最後一季登記截止日往前 lag_days 天，取當天或之前最近的月底。

    Args:
        last_season: 納入的最後一個季別。
        lag_days: 登記截止日往前推的天數（實價登錄申報有延遲）。
        min_date: 分析起日；截止日不得早於它。

    Returns:
        截止日（月底）。

    Raises:
        SourceValidationError: 截止日早於 min_date。
    """
    target = registration_period(last_season)[1] - timedelta(days=lag_days)
    if (target + timedelta(days=1)).month == target.month:
        # 不是月底：退到上個月的最後一天
        target = target.replace(day=1) - timedelta(days=1)
    if target < min_date:
        raise SourceValidationError(
            f'as_of_date {target} precedes min_transaction_date {min_date} ({last_season})'
        )
    return target


def discover_seasons(
    baseline: list[str], landing: Path, *, fetch: Callable | None = None, max_probe: int = 4
) -> list[str]:
    """從基準清單最後一季往後探測，把官方已發布的新季別接上去。

    Args:
        baseline: 設定檔中的季別清單。
        landing: 落地目錄（探測到的新季檔會一併快取）。
        fetch: 下載函式 fetch(season, landing)；預設為 load_bigquery.fetch_season。
        max_probe: 最多探測的新季數。

    Returns:
        基準清單加上新發布的季別。

    Raises:
        requests.RequestException: 網路錯誤不吞掉，交由呼叫端重試。
    """
    fetch = fetch or fetch_season
    seasons = list(baseline)
    candidate = baseline[-1]
    for _ in range(max_probe):
        candidate = next_season(candidate)
        try:
            fetch(candidate, landing)
        except SourceValidationError:
            LOGGER.info('Season %s is not published yet', candidate)
            break
        seasons.append(candidate)
    new_count = len(seasons) - len(baseline)
    if new_count > 1:
        LOGGER.warning('Discovered %d new seasons at once: %s', new_count, seasons[len(baseline) :])
    return seasons


def expected_tables(seasons: list[str], project: str, models_dir: Path) -> list[str]:
    """列出三層（bronze／silver／gold）應存在的資料表完整 ID。

    Args:
        seasons: 季別清單。
        project: GCP 專案。
        models_dir: dbt models 目錄（含 staging 與 marts）。

    Returns:
        排序後的資料表 ID 清單。
    """
    tables = [f'{project}.housing_bronze.lvr_season_a_{season}' for season in seasons]
    for folder, dataset in (('staging', 'housing_silver'), ('marts', 'housing_gold')):
        tables.extend(
            f'{project}.{dataset}.{path.stem}' for path in (models_dir / folder).glob('*.sql')
        )
    return sorted(tables)


def table_status(
    client: bigquery.Client, table_ids: list[str], now: datetime, refresh_days: int
) -> tuple[list[str], list[str]]:
    """檢查資料表是否缺少或即將到期。

    Args:
        client: BigQuery client。
        table_ids: 要檢查的資料表完整 ID。
        now: 目前時間（含時區）。
        refresh_days: 距到期不足幾天就視為快到期。

    Returns:
        (缺少的表, 快到期的表)，皆保持輸入順序。
    """
    missing: list[str] = []
    expiring: list[str] = []
    threshold = now + timedelta(days=refresh_days)
    for table_id in table_ids:
        try:
            table = client.get_table(table_id, timeout=20)
        except NotFound:
            missing.append(table_id)
            continue
        if table.expires is not None and table.expires < threshold:
            expiring.append(table_id)
    return missing, expiring


def read_last_build(client: bigquery.Client, project: str) -> dict | None:
    """讀取上次成功建置的狀態（housing_gold.build_metadata 的單列）。

    Args:
        client: BigQuery client。
        project: GCP 專案。

    Returns:
        {'seasons': list, 'min_transaction_date': ISO 字串, 'as_of_date': ISO 字串}；
        表不存在時回傳 None。
    """
    sql = (
        'select seasons, min_transaction_date, as_of_date '
        f'from `{project}.housing_gold.build_metadata` limit 1'
    )
    try:
        rows = list(client.query(sql).result(timeout=60))
    except NotFound:
        return None
    if not rows:
        return None
    row = rows[0]
    return {
        'seasons': str(row.seasons).split(','),
        'min_transaction_date': row.min_transaction_date.isoformat(),
        'as_of_date': row.as_of_date.isoformat(),
    }


def build_plan(
    config: dict,
    client: bigquery.Client,
    landing: Path,
    models_dir: Path,
    now: datetime,
    *,
    project: str = 'taiwan-household',
    fetch: Callable | None = None,
    refresh_days: int | None = None,
) -> dict:
    """彙整新季別、截止日與資料表狀態，產出本次排程的執行計畫。

    Args:
        config: config/seasons.json 的內容。
        client: BigQuery client。
        landing: 落地目錄。
        models_dir: dbt models 目錄。
        now: 目前時間（含時區）。
        project: GCP 專案。
        fetch: 下載函式；預設為 load_bigquery.fetch_season。
        refresh_days: 到期門檻天數；None 時取 config['refresh_before_expiry_days']。

    Returns:
        計畫 dict，鍵為 seasons、new_seasons、min_transaction_date、as_of_date、last_build、
        missing_tables、expiring_tables、reasons、should_run、dbt_vars。

    Raises:
        SourceValidationError: 截止日早於起日等設定不合理的情況。
    """
    if refresh_days is None:
        refresh_days = config['refresh_before_expiry_days']
    baseline = list(config['seasons'])
    min_date = date.fromisoformat(config['min_transaction_date'])
    seasons = discover_seasons(baseline, landing, fetch=fetch)
    as_of = as_of_for(seasons[-1], config['as_of_lag_days'], min_date)
    tables = expected_tables(seasons, project, models_dir)
    missing, expiring = table_status(client, tables, now, refresh_days)
    last_build = read_last_build(client, project)
    dbt_vars = {
        'seasons': seasons,
        'min_transaction_date': min_date.isoformat(),
        'as_of_date': as_of.isoformat(),
    }
    reasons: list[str] = []
    if last_build is None:
        reasons.append('no_successful_build')
    elif last_build != dbt_vars:
        reasons.append('state_changed')
    if missing:
        reasons.append('missing_tables')
    if expiring:
        reasons.append('expiring_tables')
    return {
        'seasons': seasons,
        'new_seasons': seasons[len(baseline) :],
        'min_transaction_date': dbt_vars['min_transaction_date'],
        'as_of_date': dbt_vars['as_of_date'],
        'last_build': last_build,
        'missing_tables': missing,
        'expiring_tables': expiring,
        'reasons': reasons,
        'should_run': bool(reasons),
        'dbt_vars': dbt_vars,
    }


def should_proceed(plan: dict, force: bool) -> bool:
    """決定是否往下執行：計畫要求執行，或使用者強制。

    Args:
        plan: build_plan 的結果。
        force: 是否強制全量執行。

    Returns:
        True 表示繼續。
    """
    return bool(plan['should_run'] or force)


def main(argv: list[str] | None = None) -> int:
    """命令列進入點。

    Args:
        argv: 參數清單；None 時使用 sys.argv。

    Returns:
        exit code：0 成功，3 為確定性錯誤（設定不合理或缺少憑證，不應重試）。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    plan_parser = sub.add_parser('plan', help='輸出本次排程的執行計畫（JSON）')
    plan_parser.add_argument('--landing', type=Path, default=ROOT / 'data/landing')
    plan_parser.add_argument('--project', default='taiwan-household')
    plan_parser.add_argument('--refresh-days', type=int, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    config = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    try:
        client = bigquery.Client(project=args.project)
        plan = build_plan(
            config,
            client,
            args.landing,
            MODELS_DIR,
            datetime.now(UTC),
            project=args.project,
            refresh_days=args.refresh_days,
        )
    except (SourceValidationError, DefaultCredentialsError) as exc:
        LOGGER.error('Planning failed: %s', exc)
        return 3
    # stdout 給程式解析：純 ASCII，不受 Windows 主控台 cp950 編碼影響
    sys.stdout.write(json.dumps(plan, ensure_ascii=True, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
