import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import FIELDS, ROOT, load_raw, validate_archive


def row(serial='A001', **overrides):
    values = ['大安區', '房地(土地+建物)+車位', '1140715', '12000000', '100', '2000000', '20', '', serial]
    result = dict(zip(FIELDS, values))
    result.update(overrides)
    return result


def archive(rows, encoding='utf-8-sig'):
    text = io.StringIO(newline='')
    writer = csv.DictWriter(text, fieldnames=list(FIELDS))
    writer.writeheader()
    writer.writerow({k: 'serial number' if k == '編號' else 'English header' for k in FIELDS})
    writer.writerows(rows)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w') as z:
        z.writestr('manifest.csv', 'name\na_lvr_land_a.csv')
        z.writestr('build_time.xml', '<lvr_land/>')
        z.writestr('a_lvr_land_a.csv', text.getvalue().encode(encoding))
    return payload.getvalue()


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'cp950'])
def test_csv_encoding_and_english_header(encoding):
    rows, actual, _ = validate_archive(archive([row()], encoding))
    assert len(rows) == 1
    assert rows[0]['編號'] == 'A001'
    assert actual == encoding


def test_html_response_is_rejected():
    with pytest.raises(ValueError, match='not a ZIP'):
        validate_archive(b'<html>error</html>' * 30)


def test_missing_schema_is_rejected():
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w') as z:
        z.writestr('manifest.csv', '')
        z.writestr('build_time.xml', '<root/>')
        z.writestr('a_lvr_land_a.csv', 'wrong\nvalue')
    with pytest.raises(ValueError, match='schema mismatch'):
        validate_archive(payload.getvalue())


def test_replay_dedup_rules_and_publication_gate(tmp_path):
    rows = [row(), row('A002', 備註='陽台外推'),
            row('A003', 備註='親友、員工、共有人或其他特殊關係間之交易'), row('A004', 交易年月日='1140230'),
            row('A005', 交易年月日='1190101'), row('A006', 交易標的='土地'),
            row('A007', 備註='預售屋、或土地及建物分件登記'),
            row('A008', 車位總價元=''), row('A009', 交易年月日='1130101')]
    path = tmp_path / 'fixture.zip'
    path.write_bytes(archive(rows))
    cmd = [sys.executable, str(ROOT / 'pipeline.py'), '--zip', str(path),
           '--batch-id', 'fixture', '--source-date', '2025-12-10',
           '--as-of', '2026-11-15', '--data-dir', str(tmp_path / 'data')]
    for _ in range(2):
        subprocess.run(cmd, check=True, capture_output=True)
    db_path = tmp_path / 'data/housing.duckdb'
    with duckdb.connect(str(db_path)) as db:
        assert db.sql('select count(*) from raw.lvr_sale').fetchone()[0] == 9
        assert db.sql('select count(*) from fct_housing_deal').fetchone()[0] == 2
        price = db.sql("select price_per_ping from fct_housing_deal where serial_no='A001'").fetchone()[0]
        assert price == pytest.approx(10_000_000 / 80 * 3.30579)
        reasons = dict(db.sql('select serial_no, exclusion_reason from stg_lvr_sale').fetchall())
        assert reasons['A002'] == 'eligible'
        assert reasons['A004'] == 'invalid_date'
        assert reasons['A005'] == 'future_date'
        assert reasons['A008'] == 'invalid_price_or_area'
    # Older backfill must not replace a newer transaction revision.
    load_raw(db_path, [row(總價元='1')], 'older', '2024-12-10')
    subprocess.run(cmd, check=True, capture_output=True)
    with duckdb.connect(str(db_path)) as db:
        assert db.sql("select total_price from stg_lvr_sale where serial_no='A001'").fetchone()[0] == '12000000'
    before = db_path.read_bytes()
    # All rows excluded => nonempty_mart fails; published bytes must stay intact.
    failed = subprocess.run(cmd + ['--min-date', '2026-01-01'], capture_output=True)
    assert failed.returncode != 0
    assert db_path.read_bytes() == before


def test_app_missing_database(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('HOUSING_DATA_DIR', str(tmp_path))
    app = AppTest.from_file(str(ROOT / 'app.py')).run()
    assert not app.exception
    assert len(app.info) == 1
