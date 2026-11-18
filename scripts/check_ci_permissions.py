"""證明目前的憑證（CI service account）無法寫入正式的 housing_silver／housing_gold。

Invoke from the project root with: python -m scripts.check_ci_permissions
exit code：0 兩個正式 dataset 都拒絕寫入；1 至少一個可以寫入（權限過大）。
"""

import argparse
import logging
import sys

from google.api_core.exceptions import Forbidden
from google.cloud import bigquery

LOGGER = logging.getLogger(__name__)
PROD_DATASETS = ('housing_silver', 'housing_gold')
PROBE_TABLE = '_ci_write_probe'


def probe_prod_writes(client: bigquery.Client, project: str) -> dict[str, str]:
    """嘗試在每個正式 dataset 建一張探測表。

    Args:
        client: 以 CI 憑證建立的 BigQuery client。
        project: GCP 專案。

    Returns:
        {dataset: 'denied' 或 'writable'}；可寫入時會立刻刪除探測表。
    """
    result: dict[str, str] = {}
    for dataset in PROD_DATASETS:
        table_id = f'{project}.{dataset}.{PROBE_TABLE}'
        try:
            client.query(f'create table `{table_id}` as select 1 as probe').result(timeout=60)
        except Forbidden:
            result[dataset] = 'denied'
            continue
        client.delete_table(table_id, not_found_ok=True)
        result[dataset] = 'writable'
    return result


def main(argv: list[str] | None = None) -> int:
    """命令列進入點。

    Args:
        argv: 參數清單；None 時使用 sys.argv。

    Returns:
        exit code：0 正式資料受保護，1 權限過大。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default='taiwan-household')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    result = probe_prod_writes(bigquery.Client(project=args.project), args.project)
    for dataset, status in result.items():
        LOGGER.info('%s: %s', dataset, status)
    if any(status != 'denied' for status in result.values()):
        LOGGER.error('CI credentials can write production datasets; revoke the extra roles')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
