"""Load one official season ZIP of Taipei sales into BigQuery, one table per season.

Invoke from the project root with: python -m scripts.load_bigquery --season 115S3
Each season gets its own table (lvr_season_a_<season>); reruns replace only that season.
"""
import argparse
import hashlib
import io
import json
import logging
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import pandas as pd
import requests
from google.cloud import bigquery

from scripts.profile_dataset import read_sales

ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)

SEASON_PATTERN = re.compile(r'^\d{3}S[1-4]$')
SEASON_URL = 'https://plvr.land.moi.gov.tw/DownloadSeason?season={season}&type=zip&fileName=lvr_landcsv.zip'
ZIP_MAGIC = b'PK\x03\x04'
MAX_DOWNLOAD_BYTES = 100_000_000
MAX_UNCOMPRESSED_BYTES = 500_000_000
REQUIRED_MEMBERS = {'manifest.csv', 'build_time.xml', 'a_lvr_land_a.csv'}
USER_AGENT = 'taiwan-housing-pipeline/1.0'
# 買賣案件的登記期間；租賃與預售屋的日期不在此段，必須由「之買賣案件」錨定。
SALES_PERIOD_PATTERN = re.compile(
    r'登記日期\s*自?\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日?\s*至\s*'
    r'(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日?\s*之買賣案件')
COLUMN_TYPES = {'source_row_number': 'INTEGER', 'loaded_at': 'TIMESTAMP'}


class SourceValidationError(ValueError):
    """來源資料的確定性錯誤；重試也不會成功，呼叫端應停止而非重試。"""


def parse_season(season: str) -> tuple[int, int]:
    """解析季別字串。

    Args:
        season: 季別，格式為 3 位民國年 + S + 季（1-4），例如 115S3。

    Returns:
        (民國年, 季) 的 tuple。

    Raises:
        SourceValidationError: 格式不符。
    """
    if not isinstance(season, str) or not SEASON_PATTERN.fullmatch(season):
        raise SourceValidationError(f'Invalid season format: {season!r} (expected e.g. 115S3)')
    return int(season[:3]), int(season[-1])


def season_url(season: str) -> str:
    """回傳官方季檔下載網址。

    Args:
        season: 季別，例如 115S3。

    Returns:
        官方下載網址。
    """
    return SEASON_URL.format(season=season)


def registration_period(season: str) -> tuple[date, date]:
    """計算季別對應的買賣登記期間。

    Args:
        season: 季別，例如 115S3。

    Returns:
        (起日, 迄日)，含頭尾。
    """
    roc_year, quarter = parse_season(season)
    year = roc_year + 1911
    if quarter == 1:
        return date(year - 1, 12, 11), date(year, 3, 10)
    if quarter == 2:
        return date(year, 3, 11), date(year, 6, 10)
    if quarter == 3:
        return date(year, 6, 11), date(year, 9, 10)
    return date(year, 9, 11), date(year, 12, 10)


def check_period(build_time_xml: str, season: str) -> None:
    """檢查 build_time.xml 的買賣登記期間是否等於季別預期值。

    Args:
        build_time_xml: build_time.xml 的文字內容。
        season: 預期的季別。

    Raises:
        SourceValidationError: 找不到買賣登記期間，或起訖日不符。
    """
    expected = registration_period(season)
    match = SALES_PERIOD_PATTERN.search(build_time_xml)
    if match is None:
        raise SourceValidationError('Sales registration period not found in build_time.xml')
    numbers = [int(value) for value in match.groups()]
    try:
        actual = (date(numbers[0] + 1911, numbers[1], numbers[2]),
                  date(numbers[3] + 1911, numbers[4], numbers[5]))
    except ValueError as exc:
        raise SourceValidationError(f'Invalid date in build_time.xml: {exc}') from exc
    if actual != expected:
        raise SourceValidationError(
            f'Registration period {actual[0]}~{actual[1]} does not match {season} '
            f'({expected[0]}~{expected[1]})')


def _sha256_of(path: Path) -> str:
    """計算檔案的 sha256。"""
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _cached_zip(season: str, landing: Path) -> Path | None:
    """回傳有效快取的 ZIP 路徑；快取不存在或損毀時回傳 None。"""
    index_path = landing / 'seasons' / f'{season}.json'
    if not index_path.is_file():
        return None
    try:
        index = json.loads(index_path.read_text(encoding='utf-8'))
        digest = index['sha256']
    except (json.JSONDecodeError, KeyError, TypeError, OSError):
        return None
    zip_path = landing / str(digest) / 'source.zip'
    if zip_path.is_file() and _sha256_of(zip_path) == digest:
        return zip_path
    return None


def fetch_season(season: str, landing: Path, *, refresh: bool = False,
                 session: requests.Session | None = None) -> Path:
    """下載（或重用快取的）官方季檔 ZIP。

    Args:
        season: 季別，例如 115S3。
        landing: 落地目錄；ZIP 存於 landing/<sha256>/source.zip，索引存於 landing/seasons/。
        refresh: 為 True 時忽略快取，重新下載。
        session: 可注入的 requests.Session；預設建立新的。

    Returns:
        source.zip 的路徑。

    Raises:
        SourceValidationError: 季別格式錯誤、超過下載上限，或內容不是 ZIP（例如未發布）。
        requests.RequestException: HTTP 或連線錯誤（可重試，不包裝）。
    """
    parse_season(season)
    previous_sha = None
    if not refresh:
        cached = _cached_zip(season, landing)
        if cached is not None:
            return cached
    else:
        index_path = landing / 'seasons' / f'{season}.json'
        if index_path.is_file():
            try:
                previous_sha = json.loads(index_path.read_text(encoding='utf-8')).get('sha256')
            except (json.JSONDecodeError, AttributeError, OSError):
                previous_sha = None
    session = session or requests.Session()
    url = season_url(season)
    chunks: list[bytes] = []
    total = 0
    with session.get(url, stream=True, timeout=(10, 120),
                     headers={'User-Agent': USER_AGENT}) as response:
        response.raise_for_status()
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                raise SourceValidationError(f'Download exceeds {MAX_DOWNLOAD_BYTES} bytes')
            if chunks[0][:4] != ZIP_MAGIC and total >= 4:
                break
    payload = b''.join(chunks)
    if not payload.startswith(ZIP_MAGIC):
        raise SourceValidationError(f'{season} is not a ZIP (season not published yet?)')
    digest = hashlib.sha256(payload).hexdigest()
    zip_path = landing / digest / 'source.zip'
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    zip_path.write_bytes(payload)
    index_path = landing / 'seasons' / f'{season}.json'
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index = {'season': season, 'sha256': digest, 'url': url,
             'downloaded_at': datetime.now(UTC).isoformat()}
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
    if refresh and previous_sha and previous_sha != digest:
        LOGGER.warning('Season %s content changed on refresh: %s -> %s', season, previous_sha, digest)
    return zip_path


def prepare(path: Path, season: str, landing: Path) -> tuple[pd.DataFrame, dict]:
    """驗證季檔 ZIP 並轉成載入用的 DataFrame。

    Args:
        path: 官方季檔 ZIP 路徑。
        season: 預期的季別。
        landing: 落地目錄；來源與 metadata 會寫入 landing/<sha256>/。

    Returns:
        (DataFrame, metadata)。DataFrame 保留原始字串並加上血緣欄位。

    Raises:
        SourceValidationError: 季別、ZIP 結構、CRC、編碼、欄位、空檔或登記期間不符。
    """
    parse_season(season)
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    try:
        with ZipFile(io.BytesIO(payload)) as archive:
            if not REQUIRED_MEMBERS.issubset(archive.namelist()):
                raise SourceValidationError('Missing required ZIP members')
            if sum(i.file_size for i in archive.infolist()) > MAX_UNCOMPRESSED_BYTES:
                raise SourceValidationError('Archive exceeds 500 MB uncompressed limit')
            if archive.testzip():
                raise SourceValidationError('ZIP CRC failed')
            try:
                df, encoding = read_sales(archive.read('a_lvr_land_a.csv'))
                period_xml = archive.read('build_time.xml').decode('utf-8-sig')
            except (ValueError, UnicodeDecodeError) as exc:
                raise SourceValidationError(str(exc)) from exc
    except BadZipFile as exc:
        raise SourceValidationError(f'Invalid ZIP file: {exc}') from exc
    check_period(period_xml, season)
    mapping = json.loads((ROOT / 'config/column_mapping.json').read_text(encoding='utf-8'))
    if set(df.columns) != set(mapping) or len(df.columns) != len(mapping):
        raise SourceValidationError('Source schema changed: review column_mapping.json before loading')
    df = df.rename(columns=mapping)
    # Preserve raw values and row order; validation is not business filtering.
    start, end = registration_period(season)
    df['city_code'] = 'A'
    df['season'] = season
    df['source_sha256'] = digest
    df['source_row_number'] = range(1, len(df) + 1)
    df['loaded_at'] = datetime.now(UTC)
    metadata = {
        'sha256': digest, 'season': season, 'source_rows': len(df),
        'registration_start': start.isoformat(), 'registration_end': end.isoformat(),
        'encoding': encoding, 'source_file': 'a_lvr_land_a.csv', 'source_period_xml': period_xml,
    }
    target = landing / digest
    target.mkdir(parents=True, exist_ok=True)
    (target / 'source.zip').write_bytes(payload)
    (target / 'metadata.json').write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    return df, metadata


def table_id_for(project: str, dataset: str, season: str) -> str:
    """組出季別資料表的完整 ID。

    Args:
        project: GCP 專案。
        dataset: BigQuery dataset。
        season: 季別。

    Returns:
        project.dataset.lvr_season_a_<season>。
    """
    return f'{project}.{dataset}.lvr_season_a_{season}'


def bigquery_schema(columns: list[str]) -> list[bigquery.SchemaField]:
    """依欄位名稱建立 BigQuery schema；未列於對照表者一律為 STRING。

    Args:
        columns: 欄位名稱清單。

    Returns:
        SchemaField 清單。
    """
    return [bigquery.SchemaField(c, COLUMN_TYPES.get(c, 'STRING')) for c in columns]


def load(client: bigquery.Client, df: pd.DataFrame, metadata: dict, table_id: str,
         location: str) -> dict:
    """以 WRITE_TRUNCATE 載入單一季別資料表並核對筆數。

    Args:
        client: BigQuery client。
        df: prepare 產生的 DataFrame。
        metadata: prepare 產生的 metadata。
        table_id: 目標資料表完整 ID。
        location: 預期的 dataset 區域。

    Returns:
        metadata 的副本，加上 table_id、loaded_rows、job_id、location、table_expires、status。

    Raises:
        SourceValidationError: dataset 區域與預期不符。
        RuntimeError: 載入後筆數與來源不符。
    """
    dataset_id = table_id.rsplit('.', 1)[0]
    dataset = client.get_dataset(dataset_id, timeout=20)
    if dataset.location.upper() != location.upper():
        raise SourceValidationError(f'Dataset location is {dataset.location}, not {location}')
    config = bigquery.LoadJobConfig(schema=bigquery_schema(list(df.columns)),
                                    write_disposition='WRITE_TRUNCATE')
    job = client.load_table_from_dataframe(df, table_id, location=location,
                                           job_config=config, timeout=30)
    job.result(timeout=120)
    table = client.get_table(table_id, timeout=20)
    if table.num_rows != len(df):
        raise RuntimeError(f'Row count mismatch: source={len(df)}, BigQuery={table.num_rows}')
    expires = getattr(table, 'expires', None)
    return {**metadata, 'table_id': table_id, 'loaded_rows': table.num_rows, 'job_id': job.job_id,
            'location': table.location, 'table_expires': expires.isoformat() if expires else None,
            'status': 'row_count_verified'}


def drop_season(client: bigquery.Client, table_id: str) -> None:
    """刪除單一季別資料表（不存在時不報錯）。

    Args:
        client: BigQuery client。
        table_id: 要刪除的資料表完整 ID。
    """
    client.delete_table(table_id, not_found_ok=True)


def main(argv: list[str] | None = None) -> int:
    """命令列進入點。

    Args:
        argv: 參數清單；None 時使用 sys.argv。

    Returns:
        exit code：0 成功，3 為來源驗證失敗（不應重試）。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--season', required=True)
    parser.add_argument('--zip', type=Path, help='本機季檔 ZIP；未給則下載或使用快取')
    parser.add_argument('--landing', type=Path, default=ROOT / 'data/landing')
    parser.add_argument('--project', default='taiwan-household')
    parser.add_argument('--dataset', default='housing_raw')
    parser.add_argument('--location', default='US')
    parser.add_argument('--refresh', action='store_true')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--validate-only', action='store_true')
    mode.add_argument('--drop', action='store_true')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    try:
        parse_season(args.season)
        table_id = table_id_for(args.project, args.dataset, args.season)
        if args.drop:
            drop_season(bigquery.Client(project=args.project), table_id)
            return 0
        source = args.zip or fetch_season(args.season, args.landing, refresh=args.refresh)
        df, metadata = prepare(source, args.season, args.landing)
        metadata['table_id'] = table_id
        if args.validate_only:
            result = metadata
        else:
            client = bigquery.Client(project=args.project)
            result = load(client, df, metadata, table_id, args.location)
            (args.landing / metadata['sha256'] / 'load_result.json').write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    except SourceValidationError as exc:
        LOGGER.error('Source validation failed: %s', exc)
        return 3
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
