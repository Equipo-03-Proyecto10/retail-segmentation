"""Load the evaluation dataset into a throwaway database (F23-01).

    python -m analysis.load --database-url postgresql://postgres@localhost:55434/retail

The database must already hold the three SQL scripts, which supply the stores,
channels and products these rows point at. Every sale is SEEDED (ADR-0052), so any
run over them is marked Synthetic. A URL that is not on this machine is refused:
this data must never reach the instance.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import psycopg
from psycopg import Connection

from analysis.dataset import (
    DEFAULT_CUSTOMERS,
    DEFAULT_SEED,
    HISTORY_DAYS,
    Dataset,
    generate,
)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_REGISTRATION_CHANNEL = 3

_CUSTOMER = """
INSERT INTO customer (customer_id, name, registration_channel_id, registered_on)
VALUES (%s, %s, %s, %s)
"""
_SALE = """
INSERT INTO transaction (source_transaction_id, customer_id, store_id, channel_id,
                         occurred_at, total, data_origin)
VALUES (%s, %s, %s, %s, %s, %s, 'SEEDED')
"""
# A seed product's category is its id for ids 1 to 30, so a top-level category names
# a real product. One line of one unit keeps total equal to the sum of its lines.
_LINE = """
INSERT INTO transaction_line (transaction_id, product_id, quantity, unit_price)
SELECT transaction_id, %s, 1, %s FROM transaction WHERE source_transaction_id = %s
"""


def load(
    connection: Connection[Any], dataset: Dataset, as_of: datetime | None = None
) -> None:
    """Insert the rows in the caller's transaction, which also owns the commit."""
    moment = as_of or datetime.now(UTC)
    registered = (moment - timedelta(days=HISTORY_DAYS + 1)).date()
    sale_ids = [
        f"EVAL-{dataset.seed}-{number:08d}" for number in range(len(dataset.sales))
    ]
    with connection.cursor() as cursor:
        cursor.executemany(
            _CUSTOMER,
            [
                (
                    customer.customer_id,
                    f"Evaluation customer {number}",
                    _REGISTRATION_CHANNEL,
                    registered,
                )
                for number, customer in enumerate(dataset.customers)
            ],
        )
        cursor.executemany(
            _SALE,
            [
                (
                    sale_id,
                    sale.customer_id,
                    sale.store,
                    sale.channel,
                    sale.occurred_at(moment),
                    sale.amount,
                )
                for sale_id, sale in zip(sale_ids, dataset.sales, strict=True)
            ],
        )
        cursor.executemany(
            _LINE,
            [
                (sale.category, sale.amount, sale_id)
                for sale_id, sale in zip(sale_ids, dataset.sales, strict=True)
            ],
        )


def is_local(url: str) -> bool:
    return (urlparse(url).hostname or "localhost") in _LOCAL_HOSTS


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--customers", type=int, default=DEFAULT_CUSTOMERS)
    arguments = parser.parse_args(argv)
    if not is_local(arguments.database_url):
        print("Refused: the evaluation dataset loads only into a local database.")
        return 1
    dataset = generate(arguments.seed, arguments.customers)
    with psycopg.connect(arguments.database_url) as connection:
        load(connection, dataset)
    print(
        f"{len(dataset.customers)} customers, {len(dataset.sales)} sales, "
        f"fingerprint {dataset.fingerprint()}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
