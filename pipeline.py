"""Download or replay an MOI archive, build models, publish only after tests pass."""
import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import date

ROOT = Path(__file__).resolve().parent
FIELDS = {
    '鄉鎮市區': 'district', '交易標的': 'transaction_target',
    '交易年月日': 'transaction_roc', '總價元': 'total_price',
    '建物移轉總面積平方公尺': 'building_area',
    '車位總價元': 'parking_price', '車位移轉總面積平方公尺': 'parking_area',
    '備註': 'notes', '編號': 'serial_no',
}


def decode_csv(payload):
    for encoding in ('utf-8-sig', 'cp950', 'big5'):
        try:
            return payload.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise ValueError('CSV is neither UTF-8 nor supported Big5')


def validate_archive(payload):
    if len(payload) < 100 or not zipfile.is_zipfile(io.BytesIO(payload)):
        raise ValueError('Response is not a ZIP (possibly an HTTP 200 HTML error page)')
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = {n.lower(): n for n in archive.namelist()}
        for name in ('manifest.csv', 'build_time.xml', 'a_lvr_land_a.csv'):
            if name not in entries:
                raise ValueError(f'Missing archive member: {name}')
        if sum(i.file_size for i in archive.infolist()) > 500_000_000:
            raise ValueError('Archive exceeds 500 MB uncompressed limit')
        if archive.testzip() is not None:
            raise ValueError('ZIP checksum failure')
        content, encoding = decode_csv(archive.read(entries['a_lvr_land_a.csv']))
        reader = csv.DictReader(io.StringIO(content))
        if not reader.fieldnames or not set(FIELDS).issubset(reader.fieldnames):
            raise ValueError(f'CSV schema mismatch; required fields: {list(FIELDS)}')
        rows = list(reader)
        if rows and rows[0]['編號'].strip().lower() == 'serial number':
            rows.pop(0)
        if not rows:
            raise ValueError('Empty Taipei sales file')
        for row in rows:
            if None in row or any(row.get(k) is None for k in FIELDS):
                raise ValueError('Malformed CSV row')
            if not row['編號'].strip():
                raise ValueError('Missing transaction serial number')
        return rows, encoding, archive.read(entries['build_time.xml']).decode('utf-8-sig')


def download(season):
    if not re.fullmatch(r'\d{3}S[1-4]', season):
        raise ValueError('Season must look like 114S4')
    url = f'https://plvr.land.moi.gov.tw/DownloadSeason?season={season}&type=zip&fileName=lvr_landcsv.zip'
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'TaiwanHousingPortfolio/1.0'})
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read(150_000_001)
            if len(payload) > 150_000_000:
                raise ValueError('Download exceeds 150 MB limit')
            validate_archive(payload)
            return payload, url
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def load_raw(db_path, rows, batch_id, source_date):
    import duckdb
    cols = list(FIELDS.values())
    with duckdb.connect(str(db_path)) as db:
        db.execute('create schema if not exists raw')
        definitions = ', '.join(f'{c} varchar' for c in cols)
        db.execute(f'''create table if not exists raw.lvr_sale (
            {definitions}, city_code varchar, batch_id varchar, source_date date,
            row_number integer, loaded_at timestamp default current_timestamp)''')
        db.execute('begin')
        try:
            db.execute('delete from raw.lvr_sale where batch_id = ?', [batch_id])
            values = [[r[k] for k in FIELDS] + ['A', batch_id, source_date, i]
                      for i, r in enumerate(rows, start=1)]
            db.executemany(f"insert into raw.lvr_sale ({', '.join(cols)}, city_code, batch_id, source_date, row_number) values ({','.join(['?'] * (len(cols)+4))})", values)
            db.execute('commit')
        except Exception:
            db.execute('rollback')
            raise


def run(args):
    import duckdb
    data_dir = Path(args.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.batch_id):
        raise ValueError('batch-id must contain only letters, digits, _ or -')
    if args.zip:
        payload, source = Path(args.zip).read_bytes(), str(Path(args.zip).resolve())
    else:
        payload, source = download(args.season)
    rows, encoding, build_time = validate_archive(payload)
    digest = hashlib.sha256(payload).hexdigest()
    landing = data_dir / 'landing' / args.batch_id / digest
    landing.mkdir(parents=True, exist_ok=True)
    (landing / 'source.zip').write_bytes(payload)
    metadata = dict(batch_id=args.batch_id, source_date=args.source_date,
                    source=source, sha256=digest, encoding=encoding,
                    source_rows=len(rows), build_time=build_time)
    (landing / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    # A candidate database isolates failures from the last successful published snapshot.
    published = data_dir / 'housing.duckdb'
    candidate = data_dir / 'candidate.duckdb'
    if published.exists():
        shutil.copy2(published, candidate)
    elif candidate.exists():
        candidate.unlink()
    load_raw(candidate, rows, args.batch_id, args.source_date)
    env = dict(os.environ, HOUSING_DB=str(candidate))
    executable = Path(sys.executable).parent / ('dbt.exe' if os.name == 'nt' else 'dbt')
    subprocess.run([str(executable), 'build', '--project-dir', str(ROOT / 'analytics'),
                    '--profiles-dir', str(ROOT / 'analytics'), '--vars',
                    json.dumps({'min_transaction_date': args.min_date, 'as_of_date': args.as_of})],
                   env=env, check=True)
    with duckdb.connect(str(candidate), read_only=True) as db:
        metadata['raw_rows'] = db.sql('select count(*) from raw.lvr_sale').fetchone()[0]
        metadata['staging_rows'] = db.sql('select count(*) from stg_lvr_sale').fetchone()[0]
        metadata['eligible_rows'] = db.sql('select count(*) from fct_housing_deal').fetchone()[0]
        metadata['median_price_per_ping'] = db.sql('select median(price_per_ping) from fct_housing_deal').fetchone()[0]
        metadata['exclusions'] = dict(db.sql('select * from qa_exclusions').fetchall())
    metadata.update(min_date=args.min_date, as_of=args.as_of)
    os.replace(candidate, published)
    (data_dir / 'last_run.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--zip', help='Replay a locally downloaded official ZIP')
    source.add_argument('--season', help='Official season identifier, e.g. 114S4')
    p.add_argument('--batch-id', required=True)
    p.add_argument('--source-date', required=True, type=lambda s: date.fromisoformat(s).isoformat(),
                   help='Effective source snapshot date, NOT local load time; orders revisions')
    p.add_argument('--min-date', default='2025-06-01', type=lambda s: date.fromisoformat(s).isoformat())
    p.add_argument('--as-of', default=date.today().isoformat(), type=lambda s: date.fromisoformat(s).isoformat())
    p.add_argument('--data-dir', default=str(ROOT / 'data'))
    return p


if __name__ == '__main__':
    run(parser().parse_args())
