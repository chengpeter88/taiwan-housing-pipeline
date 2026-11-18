"""scripts.check_ci_permissions 的測試：證明 CI 帳號寫不進正式 dataset。"""

from types import SimpleNamespace

from google.api_core.exceptions import Forbidden

from scripts import check_ci_permissions as guard


class FakeClient:
    def __init__(self, writable=()):
        self.writable = set(writable)
        self.queries = []
        self.deleted = []

    def query(self, sql, **kwargs):
        self.queries.append(sql)
        dataset = sql.split('`')[1].split('.')[1]
        if dataset not in self.writable:
            raise Forbidden('Access Denied: permission bigquery.tables.create denied')
        return SimpleNamespace(result=lambda **kw: [])

    def delete_table(self, table_id, **kwargs):
        self.deleted.append((table_id, kwargs.get('not_found_ok')))


def test_probe_reports_denied_for_each_prod_dataset():
    client = FakeClient()
    result = guard.probe_prod_writes(client, 'p')
    assert result == {'housing_silver': 'denied', 'housing_gold': 'denied'}
    assert all(q.startswith('create table `p.housing_') for q in client.queries)
    assert client.deleted == []


def test_probe_cleans_up_and_reports_writable():
    client = FakeClient(writable={'housing_gold'})
    result = guard.probe_prod_writes(client, 'p')
    assert result == {'housing_silver': 'denied', 'housing_gold': 'writable'}
    assert client.deleted == [(f'p.housing_gold.{guard.PROBE_TABLE}', True)]


def test_main_exit_codes(monkeypatch):
    monkeypatch.setattr(guard.bigquery, 'Client', lambda project: FakeClient())
    assert guard.main(['--project', 'p']) == 0
    monkeypatch.setattr(
        guard.bigquery, 'Client', lambda project: FakeClient(writable={'housing_silver'})
    )
    assert guard.main(['--project', 'p']) == 1
