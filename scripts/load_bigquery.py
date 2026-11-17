"""Load one immutable Taipei ZIP snapshot using pandas and the BigQuery client.

Invoke from the project root with: python -m scripts.load_bigquery --zip ...
Each file hash gets its own table; reruns replace only the same snapshot.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

from google.cloud import bigquery
from scripts.profile_dataset import read_sales

ROOT = Path(__file__).resolve().parents[1]


def prepare(path):
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    with ZipFile(io.BytesIO(payload)) as archive:
        required = {'manifest.csv', 'build_time.xml', 'a_lvr_land_a.csv'}
        if not required.issubset(archive.namelist()):
            raise ValueError('Missing required ZIP members')
        if sum(i.file_size for i in archive.infolist()) > 500_000_000:
            raise ValueError('Archive exceeds 500 MB uncompressed limit')
        if archive.testzip():
            raise ValueError('ZIP CRC failed')
        df, encoding = read_sales(archive.read('a_lvr_land_a.csv'))
        period_xml = archive.read('build_time.xml').decode('utf-8-sig')
    mapping = json.loads((ROOT / 'config/column_mapping.json').read_text(encoding='utf-8'))
    if set(df.columns) != set(mapping) or len(df.columns) != len(mapping):
        raise ValueError('Source schema changed: review column_mapping.json before loading')
    df = df.rename(columns=mapping)
    # Preserve raw values and row order; validation is not business filtering.
    df['city_code'] = 'A'
    df['source_sha256'] = digest
    df['source_row_number'] = range(1, len(df) + 1)
    df['loaded_at'] = datetime.now(timezone.utc)
    metadata = dict(sha256=digest, source_rows=len(df), encoding=encoding,
                    source_period_xml=period_xml, source_file='a_lvr_land_a.csv')
    landing = ROOT / 'data/landing' / digest
    landing.mkdir(parents=True, exist_ok=True)
    (landing / 'source.zip').write_bytes(payload)
    (landing / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    return df, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zip', type=Path, required=True)
    parser.add_argument('--project', default='taiwan-household')
    parser.add_argument('--dataset', default='housing_raw')
    parser.add_argument('--location', default='US')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    df, metadata = prepare(args.zip)
    table_id = f'{args.project}.{args.dataset}.lvr_sale_a_{metadata["sha256"]}'
    metadata['table_id'] = table_id
    if args.validate_only:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
        return
    client = bigquery.Client(project=args.project)
    dataset = client.get_dataset(f'{args.project}.{args.dataset}', timeout=20)
    if dataset.location.upper() != args.location.upper():
        raise ValueError(f'Dataset location is {dataset.location}, not {args.location}')
    schema = [bigquery.SchemaField(c, 'INTEGER' if c == 'source_row_number' else
              'TIMESTAMP' if c == 'loaded_at' else 'STRING') for c in df.columns]
    config = bigquery.LoadJobConfig(schema=schema, write_disposition='WRITE_TRUNCATE')
    job = client.load_table_from_dataframe(df, table_id, location=args.location,
                                           job_config=config, timeout=30)
    job.result(timeout=120)
    table = client.get_table(table_id, timeout=20)
    if table.num_rows != len(df):
        raise RuntimeError(f'Row count mismatch: source={len(df)}, BigQuery={table.num_rows}')
    metadata.update(loaded_rows=table.num_rows, job_id=job.job_id,
                    location=table.location, status='row_count_verified')
    (ROOT / 'data/bigquery_load_result.json').write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
