"""手動驗證 BigQuery 的實際狀態：表是否齊全、bronze 是否與官方檔一致、建置狀態與儲存用量。

Invoke from the project root with: python -m scripts.verify_bigquery [--json]
exit code：0 全部正常；1 發現問題；3 缺少 GCP 憑證。
"""

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from google.auth.exceptions import DefaultCredentialsError
from google.cloud import bigquery

from scripts.load_bigquery import NotFound
from scripts.seasons import expected_tables, read_last_build

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / 'config/seasons.json'
MODELS_DIR = ROOT / 'analytics_bigquery/models'
LOGGER = logging.getLogger(__name__)

MIB = 2**20
# BigQuery sandbox 的儲存額度是終身 10 GiB，刪除資料不會退回
LIFETIME_QUOTA_MIB = 10 * 1024
TABLE_COLUMN_WIDTH = 57


def inspect_tables(client: bigquery.Client, table_ids: list[str], now: datetime) -> list[dict]:
    """查詢每張表的存在與否、型態、筆數、大小與剩餘天數。

    Args:
        client: BigQuery client。
        table_ids: 資料表完整 ID。
        now: 目前時間（含時區）。

    Returns:
        每張表一個 dict，保持輸入順序；不存在的表數值欄位為 None。
    """
    result = []
    for table_id in table_ids:
        try:
            table = client.get_table(table_id, timeout=20)
        except NotFound:
            result.append(
                {
                    'table_id': table_id,
                    'exists': False,
                    'table_type': None,
                    'num_rows': None,
                    'mib': None,
                    'days_left': None,
                }
            )
            continue
        days_left = None if table.expires is None else (table.expires - now).days
        result.append(
            {
                'table_id': table_id,
                'exists': True,
                'table_type': table.table_type,
                'num_rows': table.num_rows,
                'mib': round((table.num_bytes or 0) / MIB, 2),
                'days_left': days_left,
            }
        )
    return result


def bronze_stats(client: bigquery.Client, project: str) -> dict[str, dict]:
    """依季別統計 bronze 的筆數與來源 sha256。

    Args:
        client: BigQuery client。
        project: GCP 專案。

    Returns:
        {季別: {'rows': 筆數, 'sha256s': 排序後的 sha256 清單}}；dataset 不存在時為空 dict。
    """
    sql = (
        'select season, count(*) as row_count, '
        'array_agg(distinct source_sha256) as sha256s '
        f'from `{project}.housing_bronze.lvr_season_a_*` group by season'
    )
    try:
        rows = list(client.query(sql).result(timeout=60))
    except NotFound:
        return {}
    return {row.season: {'rows': row.row_count, 'sha256s': sorted(row.sha256s)} for row in rows}


def source_manifest(seasons: list[str], landing: Path) -> dict[str, dict | None]:
    """從本機下載快取讀出每季官方檔的 sha256 與筆數。

    Args:
        seasons: 季別清單。
        landing: 下載快取根目錄（data/landing）。

    Returns:
        {季別: {'sha256', 'source_rows'}}；沒有快取的季別為 None。
    """
    manifest: dict[str, dict | None] = {}
    for season in seasons:
        index = landing / 'seasons' / f'{season}.json'
        if not index.exists():
            manifest[season] = None
            continue
        sha = json.loads(index.read_text(encoding='utf-8'))['sha256']
        metadata_path = landing / sha / 'metadata.json'
        if not metadata_path.exists():
            manifest[season] = None
            continue
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        manifest[season] = {'sha256': sha, 'source_rows': metadata['source_rows']}
    return manifest


def _check_bronze(seasons: list[str], bronze: dict, manifest: dict) -> tuple[list[str], list[str]]:
    """比對 bronze 每季的筆數與 sha256 是否等於官方檔。"""
    problems: list[str] = []
    notes: list[str] = []
    for season in seasons:
        if season not in bronze:
            problems.append(f'bronze 沒有季別 {season} 的資料')
            continue
        source = manifest.get(season)
        if source is None:
            notes.append(f'沒有 {season} 的本機下載快取，無法比對官方筆數')
            continue
        loaded = bronze[season]
        if loaded['rows'] != source['source_rows']:
            problems.append(
                f'{season} bronze 筆數 {loaded["rows"]} 與官方檔 {source["source_rows"]} 不符'
            )
        if loaded['sha256s'] != [source['sha256']]:
            problems.append(f'{season} bronze 的來源 sha256 與本機驗證過的官方檔不同')
    return problems, notes


def _check_build(config: dict, last_build: dict | None) -> tuple[list[str], list[str]]:
    """比對 build_metadata 與 config/seasons.json。"""
    if last_build is None:
        return ['housing_gold.build_metadata 不存在：沒有完整建置成功的紀錄'], []
    expected = config['seasons']
    built = last_build['seasons']
    if built != expected:
        if len(built) > len(expected) and built[: len(expected)] == expected:
            extra = ', '.join(built[len(expected) :])
            return [], [
                f'BigQuery 已自動納入新季別 {extra}（截止日 {last_build["as_of_date"]}），'
                '請同步 config/seasons.json 與 dbt_project.yml'
            ]
        return [f'build_metadata 的季別 {built} 與 config {expected} 不一致'], []
    problems = [
        f'build_metadata 的 {field} 為 {last_build[field]}，config 為 {config[field]}'
        for field in ('min_transaction_date', 'as_of_date')
        if last_build[field] != config[field]
    ]
    return problems, []


def find_problems(
    config: dict,
    tables: list[dict],
    bronze: dict,
    manifest: dict,
    last_build: dict | None,
) -> tuple[list[str], list[str]]:
    """彙整驗證結果。

    Args:
        config: config/seasons.json 內容。
        tables: inspect_tables 的結果。
        bronze: bronze_stats 的結果。
        manifest: source_manifest 的結果。
        last_build: read_last_build 的結果。

    Returns:
        (problems, notes)：problems 會讓驗證失敗；notes 只是提醒。
    """
    refresh_days = config.get('refresh_before_expiry_days', 14)
    problems: list[str] = []
    notes: list[str] = []
    for table in tables:
        if not table['exists']:
            problems.append(f'缺表 {table["table_id"]}')
        elif table['days_left'] is not None and table['days_left'] < refresh_days:
            notes.append(f'{table["table_id"]} 剩 {table["days_left"]} 天到期')
    for check_problems, check_notes in (
        _check_bronze(config['seasons'], bronze, manifest),
        _check_build(config, last_build),
    ):
        problems.extend(check_problems)
        notes.extend(check_notes)
    return problems, notes


def storage_summary(tables: list[dict]) -> dict:
    """估算一次全量執行寫入的儲存量，以及終身額度約可執行幾次。

    Args:
        tables: inspect_tables 的結果（view 不佔儲存）。

    Returns:
        {'mib_per_full_run', 'full_runs_per_lifetime_quota'}；沒有儲存時次數為 None。
    """
    total = round(sum(t['mib'] for t in tables if t['exists'] and t['table_type'] == 'TABLE'), 2)
    runs = int(LIFETIME_QUOTA_MIB // total) if total > 0 else None
    return {'mib_per_full_run': total, 'full_runs_per_lifetime_quota': runs}


def render(report: dict) -> str:
    """把報告轉成給人看的文字。

    Args:
        report: main 組出的報告 dict。

    Returns:
        多行文字。
    """
    width = TABLE_COLUMN_WIDTH
    lines = [f'{"table":<{width}} {"type":<6} {"rows":>8} {"MiB":>8} {"days":>5}']
    for t in report['tables']:
        if not t['exists']:
            lines.append(f'{t["table_id"]:<{width}} MISSING')
            continue
        days = '-' if t['days_left'] is None else t['days_left']
        lines.append(
            f'{t["table_id"]:<{width}} {t["table_type"]:<6} {t["num_rows"]:>8} '
            f'{t["mib"]:>8} {days:>5}'
        )
    lines.append('')
    lines.append('bronze 與官方檔比對：')
    for season, stats in sorted(report['bronze'].items()):
        source = report['manifest'].get(season)
        expected = '-' if source is None else source['source_rows']
        lines.append(f'  {season}: bronze {stats["rows"]} / 官方 {expected}')
    lines.append(f'build_metadata：{report["last_build"]}')
    storage = report['storage']
    lines.append(
        f'儲存：每次全量約 {storage["mib_per_full_run"]} MiB；'
        f'10 GiB 終身額度約可全量執行 {storage["full_runs_per_lifetime_quota"]} 次'
    )
    lines.extend(f'NOTE {note}' for note in report['notes'])
    lines.extend(f'FAIL {problem}' for problem in report['problems'])
    lines.append('結果：' + ('通過' if not report['problems'] else '有問題'))
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    """命令列進入點。

    Args:
        argv: 參數清單；None 時使用 sys.argv。

    Returns:
        exit code：0 正常，1 有問題，3 缺少憑證。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default='taiwan-household')
    parser.add_argument('--landing', type=Path, default=ROOT / 'data/landing')
    parser.add_argument('--json', action='store_true', help='輸出 ASCII JSON')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    config = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    try:
        client = bigquery.Client(project=args.project)
        table_ids = expected_tables(config['seasons'], args.project, MODELS_DIR)
        tables = inspect_tables(client, table_ids, datetime.now(UTC))
        bronze = bronze_stats(client, args.project)
        last_build = read_last_build(client, args.project)
    except DefaultCredentialsError as exc:
        LOGGER.error('Missing GCP credentials: %s', exc)
        return 3
    manifest = source_manifest(config['seasons'], args.landing)
    problems, notes = find_problems(config, tables, bronze, manifest, last_build)
    report = {
        'tables': tables,
        'bronze': bronze,
        'manifest': manifest,
        'last_build': last_build,
        'storage': storage_summary(tables),
        'notes': notes,
        'problems': problems,
    }
    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=True, indent=2) + '\n')
    else:
        # Windows 主控台可能是 cp950：無法編碼的字元以 ? 取代，不讓輸出中斷
        sys.stdout.reconfigure(errors='replace')
        sys.stdout.write(render(report) + '\n')
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
