"""Read-only Taipei sales profiling. Does not load or publish warehouse tables.

Run: python scripts/profile_dataset.py source.zip --as-of 2026-11-15
"""

import argparse
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

HOUSING = '房地(土地+建物)'
WITH_PARKING = HOUSING + '+車位'
NUMERIC = [
    '總價元',
    '建物移轉總面積平方公尺',
    '車位總價元',
    '車位移轉總面積平方公尺',
    '單價元平方公尺',
]
REQUIRED = ['編號', '交易標的', '交易年月日', '備註', '主要用途', '車位類別'] + NUMERIC


def read_sales(payload):
    """Let pandas parse CSV; never skip the first data row without checking it."""
    for encoding in ('utf-8-sig', 'cp950', 'big5'):
        try:
            text = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError('Unsupported CSV encoding')
    df = pd.read_csv(io.StringIO(text), dtype='string', keep_default_na=False)
    missing = set(REQUIRED) - set(df.columns)
    if missing:
        raise ValueError(f'Missing columns: {sorted(missing)}')
    if not df.empty and df.iloc[0]['編號'].strip().lower() == 'serial number':
        df = df.iloc[1:]
    if df.empty:
        raise ValueError('Empty sales file')
    if df.isna().any().any():
        raise ValueError('Malformed CSV: rows with missing fields')
    return df.reset_index(drop=True), encoding


def roc_dates(values):
    values = values.str.strip().where(values.str.strip().str.fullmatch(r'[0-9]{6,7}'))
    values = values.str.pad(7, side='left', fillchar='0')
    years = (pd.to_numeric(values.str[:3], errors='coerce') + 1911).astype('Int64').astype('string')
    return pd.to_datetime(years + values.str[3:], format='%Y%m%d', errors='coerce')


def parking_status(df):
    price = pd.to_numeric(df['車位總價元'], errors='coerce')
    area = pd.to_numeric(df['車位移轉總面積平方公尺'], errors='coerce')
    status = pd.Series('not_housing', index=df.index, dtype='string')
    housing = df['交易標的'].isin([HOUSING, WITH_PARKING])
    status.loc[housing] = 'unclear_or_inconsistent'
    status.loc[
        df['交易標的'].eq(HOUSING) & price.eq(0) & area.eq(0) & df['車位類別'].str.strip().eq('')
    ] = 'no_parking'
    status.loc[df['交易標的'].eq(WITH_PARKING) & price.gt(0) & area.gt(0)] = 'separately_reported'
    return status


def profile(df, min_date, as_of):
    dates = roc_dates(df['交易年月日'])
    numbers = df[NUMERIC].apply(pd.to_numeric, errors='coerce')
    housing = df['交易標的'].isin([HOUSING, WITH_PARKING])
    special = df['備註'].str.contains('親友、員工、共有人或其他特殊關係', regex=False)
    presale = df['備註'].str.contains('預售屋、或土地及建物分件登記', regex=False)
    price = numbers['總價元'] - numbers['車位總價元']
    area = numbers['建物移轉總面積平方公尺'] - numbers['車位移轉總面積平方公尺']
    baseline = (
        housing
        & ~special
        & ~presale
        & dates.between(min_date, as_of)
        & price.gt(0)
        & area.gt(0)
        & numbers['車位總價元'].ge(0)
        & numbers['車位移轉總面積平方公尺'].ge(0)
    )
    status = parking_status(df)
    strict = baseline & status.isin(['no_parking', 'separately_reported'])
    unit_price = price.div(area.where(area.gt(0))) * 3.30579
    pairs = df[['交易標的']].assign(
        parking_price_positive=numbers['車位總價元'].gt(0),
        parking_area_positive=numbers['車位移轉總面積平方公尺'].gt(0),
    )

    def counts(series: pd.Series) -> dict[str, int]:
        return {str(k): int(v) for k, v in series.value_counts(dropna=False).items()}

    def median(mask: pd.Series) -> float | None:
        return float(unit_price[mask].median()) if mask.any() else None

    return {
        'scope': 'Taipei sale main file only; no cross-batch revision inference',
        'min_date': str(min_date.date()),
        'as_of': str(as_of.date()),
        'rows': len(df),
        'columns': list(df.columns),
        'missing_serial': int(df['編號'].str.strip().eq('').sum()),
        'duplicate_serial_extra_rows': int(df['編號'].duplicated().sum()),
        'exact_duplicate_extra_rows': int(df.duplicated().sum()),
        'blank_counts': {
            k: int(v) for k, v in df.apply(lambda s: s.str.strip().eq('')).sum().items()
        },
        'invalid_numeric_nonblank_counts': {
            c: int((df[c].str.strip().ne('') & numbers[c].isna()).sum()) for c in NUMERIC
        },
        'invalid_date_rows': int(dates.isna().sum()),
        'future_date_rows': int(dates.gt(as_of).sum()),
        'date_min': None if dates.isna().all() else str(dates.min().date()),
        'date_max': None if dates.isna().all() else str(dates.max().date()),
        'targets': counts(df['交易標的']),
        'housing_parking_groups': pairs[housing]
        .groupby(list(pairs.columns))
        .size()
        .reset_index(name='rows')
        .to_dict('records'),
        'baseline_rows': int(baseline.sum()),
        'baseline_parking_status': counts(status[baseline]),
        'strict_parking_rows': int(strict.sum()),
        'baseline_median_ntd_per_ping': median(baseline),
        'strict_parking_median_ntd_per_ping': median(strict),
        'strict_parking_main_uses': counts(df.loc[strict, '主要用途']),
        'interpretation': 'Strict parking is a proposed sensitivity scenario, not proof of correct declarations. Main use is not filtered to residential.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('zip', type=Path)
    parser.add_argument('--min-date', default='2025-06-01')
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--output', type=Path, default=Path('data/dataset_profile.json'))
    args = parser.parse_args()
    min_date, as_of = pd.Timestamp(args.min_date), pd.Timestamp(args.as_of)
    if min_date > as_of:
        parser.error('min-date must not be after as-of')
    payload = args.zip.read_bytes()
    with ZipFile(io.BytesIO(payload)) as archive:
        if sum(info.file_size for info in archive.infolist()) > 500_000_000:
            raise ValueError('Archive exceeds 500 MB uncompressed limit')
        bad = archive.testzip()
        if bad:
            raise ValueError(f'Corrupt ZIP member: {bad}')
        df, encoding = read_sales(archive.read('a_lvr_land_a.csv'))
        result = profile(df, min_date, as_of)
        result.update(
            sha256=hashlib.sha256(payload).hexdigest(),
            encoding=encoding,
            archive_files=len(archive.namelist()),
            source_period_xml=archive.read('build_time.xml').decode('utf-8-sig'),
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8'
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
