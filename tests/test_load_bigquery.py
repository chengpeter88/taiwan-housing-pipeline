"""scripts/load_bigquery.py 的測試：季別解析、下載、登記期間檢查、載入與 CLI。

所有外部依賴（官方網站、BigQuery）都以假物件取代，不會連網。
"""

import io
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from scripts import load_bigquery as lb

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/build_time'
MAPPING = json.loads((ROOT / 'config/column_mapping.json').read_text(encoding='utf-8'))
ALL_SEASONS = json.loads((ROOT / 'config/seasons.json').read_text(encoding='utf-8'))['seasons']


# ---------- 測試資料工具 ----------


def make_sales_csv(rows: int = 3, columns: list[str] | None = None) -> bytes:
    """產生含中英雙表頭的台北買賣 CSV（UTF-8 BOM）。"""
    columns = columns or list(MAPPING)
    english = {c: c for c in columns}
    english['編號'] = 'serial number'
    lines = [','.join(columns), ','.join(english[c] for c in columns)]
    for i in range(rows):
        values = {c: '' for c in columns}
        values.update({'編號': f'RP{i:04d}', '交易標的': '房地(土地+建物)', '交易年月日': '1150601',
                       '總價元': '10000000', '建物移轉總面積平方公尺': '50', '車位總價元': '0',
                       '車位移轉總面積平方公尺': '0', '單價元平方公尺': '200000'})
        lines.append(','.join(values[c] for c in columns))
    return ('\n'.join(lines) + '\n').encode('utf-8-sig')


def make_zip(season: str = '115S3', csv: bytes | None = None, build_time: bytes | None = None,
             members: tuple[str, ...] = ('manifest.csv', 'build_time.xml', 'a_lvr_land_a.csv')) -> bytes:
    """產生最小的官方季檔 ZIP。"""
    content = {
        'manifest.csv': b'name\n',
        'build_time.xml': build_time if build_time is not None else (FIXTURES / f'{season}.xml').read_bytes(),
        'a_lvr_land_a.csv': csv if csv is not None else make_sales_csv(),
    }
    buffer = io.BytesIO()
    with ZipFile(buffer, 'w') as archive:
        for name in members:
            archive.writestr(name, content[name])
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, content_type: str = 'application/octet-stream'):
        self.body, self.status_code = body, status
        self.headers = {'Content-Type': content_type}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise lb.requests.HTTPError(f'HTTP {self.status_code}')

    def iter_content(self, chunk_size: int = 1):
        for start in range(0, len(self.body), max(chunk_size, 1)):
            yield self.body[start:start + chunk_size]


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response, self.calls = response, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class FakeClient:
    """模擬 bigquery.Client，記錄載入參數。"""

    def __init__(self, num_rows: int | None = None, location: str = 'US'):
        self.num_rows, self.location = num_rows, location
        self.loaded, self.deleted = [], []

    def get_dataset(self, dataset_id, **kwargs):
        return SimpleNamespace(location=self.location)

    def load_table_from_dataframe(self, df, table_id, **kwargs):
        self.loaded.append((df, table_id, kwargs))
        return SimpleNamespace(job_id='job-1', result=lambda **kw: None)

    def get_table(self, table_id, **kwargs):
        rows = self.num_rows if self.num_rows is not None else len(self.loaded[-1][0])
        return SimpleNamespace(num_rows=rows, location=self.location, expires=None)

    def delete_table(self, table_id, **kwargs):
        self.deleted.append((table_id, kwargs))


# ---------- 季別解析 ----------


@pytest.mark.parametrize('season, expected', [('115S3', (115, 3)), ('112S4', (112, 4)), ('099S1', (99, 1))])
def test_parse_season_accepts_roc_year_and_quarter(season, expected):
    assert lb.parse_season(season) == expected


@pytest.mark.parametrize('season', ['115S5', '115S0', 'abc', '115s3', '11S3', '', '115S3 '])
def test_parse_season_rejects_bad_format(season):
    with pytest.raises(lb.SourceValidationError):
        lb.parse_season(season)


def test_season_url_uses_official_download_endpoint():
    assert lb.season_url('115S3') == (
        'https://plvr.land.moi.gov.tw/DownloadSeason?season=115S3&type=zip&fileName=lvr_landcsv.zip')


@pytest.mark.parametrize('season, start, end', [
    ('115S1', date(2025, 12, 11), date(2026, 3, 10)),
    ('115S2', date(2026, 3, 11), date(2026, 6, 10)),
    ('115S3', date(2026, 6, 11), date(2026, 9, 10)),
    ('112S4', date(2023, 9, 11), date(2023, 12, 10)),
])
def test_registration_period_by_season(season, start, end):
    assert lb.registration_period(season) == (start, end)


# ---------- 登記期間檢查（真實 build_time.xml） ----------


@pytest.mark.parametrize('season', ALL_SEASONS)
def test_check_period_accepts_real_build_time(season):
    lb.check_period((FIXTURES / f'{season}.xml').read_text(encoding='utf-8-sig'), season)


def test_check_period_rejects_other_season():
    xml = (FIXTURES / '115S2.xml').read_text(encoding='utf-8-sig')
    with pytest.raises(lb.SourceValidationError):
        lb.check_period(xml, '115S3')


def test_check_period_ignores_rental_and_presale_dates():
    # 只有租賃與預售屋的期間，沒有「之買賣案件」，不可被誤判為通過
    xml = '<lvr_land><lvr_time>訂約日期 115年6月11日至 115年9月10日之租賃案件</lvr_time></lvr_land>'
    with pytest.raises(lb.SourceValidationError):
        lb.check_period(xml, '115S3')


# ---------- 下載 ----------


def test_fetch_season_downloads_and_writes_index(tmp_path):
    body = make_zip()
    session = FakeSession(FakeResponse(body))
    path = lb.fetch_season('115S3', tmp_path, session=session)
    assert path.read_bytes() == body
    assert path.name == 'source.zip'
    index = json.loads((tmp_path / 'seasons/115S3.json').read_text(encoding='utf-8'))
    assert index['season'] == '115S3'
    assert index['sha256'] == path.parent.name
    assert index['url'] == lb.season_url('115S3')
    url, kwargs = session.calls[0]
    assert url == lb.season_url('115S3')
    assert kwargs.get('stream') is True and kwargs.get('timeout')


def test_fetch_season_reuses_cache_without_network(tmp_path):
    lb.fetch_season('115S3', tmp_path, session=FakeSession(FakeResponse(make_zip())))
    session = FakeSession(FakeResponse(b'should not be used'))
    lb.fetch_season('115S3', tmp_path, session=session)
    assert session.calls == []


def test_fetch_season_refresh_downloads_again(tmp_path):
    lb.fetch_season('115S3', tmp_path, session=FakeSession(FakeResponse(make_zip())))
    newer = make_zip(csv=make_sales_csv(rows=5))
    session = FakeSession(FakeResponse(newer))
    path = lb.fetch_season('115S3', tmp_path, refresh=True, session=session)
    assert len(session.calls) == 1 and path.read_bytes() == newer


def test_fetch_season_rejects_html_even_with_zip_content_type(tmp_path):
    # 未發布季別：官方回 200 + HTML；內容判斷優先於 content-type
    html = FakeResponse(b'<!DOCTYPE html><html></html>', content_type='application/octet-stream')
    with pytest.raises(lb.SourceValidationError):
        lb.fetch_season('115S4', tmp_path, session=FakeSession(html))
    assert not (tmp_path / 'seasons/115S4.json').exists()


def test_fetch_season_enforces_download_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(lb, 'MAX_DOWNLOAD_BYTES', 100)
    with pytest.raises(lb.SourceValidationError):
        lb.fetch_season('115S3', tmp_path, session=FakeSession(FakeResponse(make_zip())))


def test_fetch_season_http_error_is_retryable_not_validation(tmp_path):
    with pytest.raises(lb.requests.HTTPError):
        lb.fetch_season('115S3', tmp_path, session=FakeSession(FakeResponse(b'', status=503)))


def test_fetch_season_validates_format_before_network(tmp_path):
    session = FakeSession(FakeResponse(make_zip()))
    with pytest.raises(lb.SourceValidationError):
        lb.fetch_season('115S5', tmp_path, session=session)
    assert session.calls == []


# ---------- prepare ----------


def test_prepare_adds_season_lineage_columns(tmp_path):
    source = tmp_path / 'in.zip'
    source.write_bytes(make_zip())
    df, metadata = lb.prepare(source, '115S3', tmp_path / 'landing')
    assert len(df) == 3  # 英文表頭列已移除
    assert set(df['season']) == {'115S3'} and set(df['city_code']) == {'A'}
    assert df['source_row_number'].tolist() == [1, 2, 3]
    assert metadata['season'] == '115S3' and metadata['source_rows'] == 3
    assert metadata['registration_start'] == '2026-06-11'
    assert metadata['registration_end'] == '2026-09-10'
    # 驗證階段就把來源固定在 landing，供載入任務使用
    assert (tmp_path / 'landing' / metadata['sha256'] / 'source.zip').read_bytes() == source.read_bytes()
    assert (tmp_path / 'landing' / metadata['sha256'] / 'metadata.json').exists()


def test_prepare_rejects_period_mismatch(tmp_path):
    source = tmp_path / 'in.zip'
    source.write_bytes(make_zip(build_time=(FIXTURES / '115S2.xml').read_bytes()))
    with pytest.raises(lb.SourceValidationError):
        lb.prepare(source, '115S3', tmp_path / 'landing')


def test_prepare_rejects_schema_change(tmp_path):
    source = tmp_path / 'in.zip'
    source.write_bytes(make_zip(csv=make_sales_csv(columns=list(MAPPING) + ['新欄位'])))
    with pytest.raises(lb.SourceValidationError):
        lb.prepare(source, '115S3', tmp_path / 'landing')


def test_prepare_rejects_missing_members(tmp_path):
    source = tmp_path / 'in.zip'
    source.write_bytes(make_zip(members=('manifest.csv', 'a_lvr_land_a.csv')))
    with pytest.raises(lb.SourceValidationError):
        lb.prepare(source, '115S3', tmp_path / 'landing')


# ---------- 載入 ----------


def test_table_id_uses_season_table_prefix():
    assert lb.table_id_for('p', 'housing_raw', '115S3') == 'p.housing_raw.lvr_season_a_115S3'


def test_bigquery_schema_types():
    fields = {f.name: f.field_type for f in lb.bigquery_schema(
        ['district', 'season', 'source_row_number', 'loaded_at'])}
    assert fields == {'district': 'STRING', 'season': 'STRING',
                      'source_row_number': 'INTEGER', 'loaded_at': 'TIMESTAMP'}


def test_load_truncates_season_table_and_verifies_rows(tmp_path):
    source = tmp_path / 'in.zip'
    source.write_bytes(make_zip())
    df, metadata = lb.prepare(source, '115S3', tmp_path / 'landing')
    client = FakeClient()
    result = lb.load(client, df, metadata, 'p.housing_raw.lvr_season_a_115S3', 'US')
    _, table_id, kwargs = client.loaded[0]
    assert table_id == 'p.housing_raw.lvr_season_a_115S3'
    assert kwargs['job_config'].write_disposition == 'WRITE_TRUNCATE'
    assert result['loaded_rows'] == 3 and result['status'] == 'row_count_verified'
    assert result['table_id'] == 'p.housing_raw.lvr_season_a_115S3'
    assert 'table_expires' in result


def test_load_raises_on_row_count_mismatch(tmp_path):
    source = tmp_path / 'in.zip'
    source.write_bytes(make_zip())
    df, metadata = lb.prepare(source, '115S3', tmp_path / 'landing')
    with pytest.raises(RuntimeError):
        lb.load(FakeClient(num_rows=2), df, metadata, 'p.housing_raw.lvr_season_a_115S3', 'US')


def test_load_rejects_dataset_in_other_location(tmp_path):
    source = tmp_path / 'in.zip'
    source.write_bytes(make_zip())
    df, metadata = lb.prepare(source, '115S3', tmp_path / 'landing')
    with pytest.raises(lb.SourceValidationError):
        lb.load(FakeClient(location='asia-east1'), df, metadata, 'p.housing_raw.lvr_season_a_115S3', 'US')


def test_drop_season_deletes_only_that_table():
    client = FakeClient()
    lb.drop_season(client, 'p.housing_raw.lvr_season_a_115S3')
    assert client.deleted == [('p.housing_raw.lvr_season_a_115S3', {'not_found_ok': True})]


# ---------- CLI ----------


def test_main_bad_season_exits_3_without_bigquery(tmp_path, monkeypatch):
    monkeypatch.setattr(lb.bigquery, 'Client', lambda **kw: pytest.fail('should not connect'))
    assert lb.main(['--season', '115S5', '--validate-only', '--landing', str(tmp_path)]) == 3


def test_main_validate_only_with_local_zip(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(lb.bigquery, 'Client', lambda **kw: pytest.fail('should not connect'))
    source = tmp_path / 'in.zip'
    source.write_bytes(make_zip())
    code = lb.main(['--season', '115S3', '--zip', str(source), '--validate-only',
                    '--landing', str(tmp_path / 'landing')])
    assert code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed['season'] == '115S3' and printed['source_rows'] == 3


def test_main_period_mismatch_exits_3(tmp_path):
    source = tmp_path / 'in.zip'
    source.write_bytes(make_zip(build_time=(FIXTURES / '115S2.xml').read_bytes()))
    assert lb.main(['--season', '115S3', '--zip', str(source), '--validate-only',
                    '--landing', str(tmp_path / 'landing')]) == 3


def test_main_load_writes_result_per_snapshot(tmp_path, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(lb.bigquery, 'Client', lambda **kw: client)
    source = tmp_path / 'in.zip'
    source.write_bytes(make_zip())
    landing = tmp_path / 'landing'
    assert lb.main(['--season', '115S3', '--zip', str(source), '--landing', str(landing)]) == 0
    results = list(landing.glob('*/load_result.json'))
    assert len(results) == 1
    assert json.loads(results[0].read_text(encoding='utf-8'))['table_id'].endswith('lvr_season_a_115S3')


def test_main_drop_season(tmp_path, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(lb.bigquery, 'Client', lambda **kw: client)
    assert lb.main(['--season', '115S3', '--drop', '--landing', str(tmp_path)]) == 0
    assert client.deleted[0][0].endswith('housing_raw.lvr_season_a_115S3')


def test_main_drop_and_validate_only_are_exclusive(tmp_path):
    with pytest.raises(SystemExit) as exc:
        lb.main(['--season', '115S3', '--drop', '--validate-only', '--landing', str(tmp_path)])
    assert exc.value.code == 2
