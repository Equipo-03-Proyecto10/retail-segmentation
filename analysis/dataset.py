"""A reproducible dataset for judging segmentation models (F23-01).

The seed holds 30 customers, which is no basis for choosing k or measuring
stability. This generates a larger population whose behaviour is known in advance,
so a study can say what it should have found. It generates in memory and writes no
file: `data/` and `*.csv` are gitignored, and the repository holds only this code.

**What is planted.** Six behaviour groups with stated shares, purchase rates and
tickets (`GROUPS`), named after the label vocabulary but independent of it; a
monthly seasonality (`SEASON`); and `EXTREME_SHARE` of customers who spend
`EXTREME_FACTOR` times their group's ticket, which is what min-max normalization is
sensitive to. Everything here is synthetic, and `analysis/load.py` marks it SEEDED
(ADR-0052).

**Why this size.** 5,000 customers leaves every group above 400, so a share within
a group is known to about five points at 95%. It also passes
`SILHOUETTE_MAX_CUSTOMERS`, so a study cannot avoid that limit by accident.

Like `web/services/kmeans.py`, this uses only `Random.random()`, the one method whose
sequence Python guarantees across versions, so a seed always gives the same data.
Times are offsets from `as_of`, because the web's RFM read measures from `now()`.
"""

from __future__ import annotations

import hashlib
import math
import random
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from web.services.kmeans import RawRfm

DEFAULT_SEED = 20261031
DEFAULT_CUSTOMERS = 5000
HISTORY_DAYS = 365
DEFAULT_WINDOW_DAYS = 180
AS_OF = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
STORES = 30
HOME_STORE_SHARE = 0.9
EXTREME_SHARE = 0.01
EXTREME_FACTOR = 25
TICKET_SPREAD = 0.45

_NAMESPACE = uuid.UUID("6f1c9f5e-3f0a-4f2a-9a54-0f23d1a7c001")
_SECONDS_PER_DAY = 86400

# Relative demand by month, January first: a December peak and a January dip.
SEASON = (0.7, 0.8, 0.9, 1.0, 1.1, 1.0, 1.0, 1.0, 0.9, 1.0, 1.3, 1.6)


@dataclass(frozen=True)
class Group:
    """One planted behaviour. Purchases per year, and when the last one was."""

    code: str
    share: float
    purchases: float
    last_purchase_days: tuple[int, int]
    ticket: float
    tenure_days: int
    categories: tuple[float, ...]
    channels: tuple[float, ...]


GROUPS = (
    Group(
        "CHAMPION",
        0.08,
        30,
        (0, 10),
        850,
        HISTORY_DAYS,
        (3, 2, 1, 2, 1, 2, 1, 2, 2, 4),
        (4, 2, 3, 1, 0),
    ),
    Group(
        "LOYAL",
        0.17,
        16,
        (0, 30),
        420,
        HISTORY_DAYS,
        (4, 3, 2, 3, 2, 2, 1, 2, 2, 1),
        (2, 1, 5, 1, 1),
    ),
    Group(
        "POTENTIAL",
        0.15,
        3,
        (0, 30),
        320,
        60,
        (2, 2, 4, 3, 1, 3, 2, 1, 1, 1),
        (5, 3, 1, 1, 0),
    ),
    Group(
        "AT_RISK",
        0.15,
        14,
        (70, 120),
        480,
        HISTORY_DAYS,
        (3, 2, 2, 2, 2, 2, 2, 2, 3, 2),
        (1, 1, 5, 1, 2),
    ),
    Group(
        "HIBERNATING",
        0.20,
        5,
        (120, 170),
        340,
        HISTORY_DAYS,
        (2, 3, 3, 3, 2, 1, 2, 2, 1, 1),
        (1, 1, 5, 2, 1),
    ),
    Group(
        "LOST",
        0.25,
        3,
        (150, 330),
        160,
        HISTORY_DAYS,
        (3, 3, 4, 3, 2, 1, 2, 1, 1, 0),
        (1, 0, 6, 1, 2),
    ),
)


@dataclass(frozen=True)
class Customer:
    customer_id: str
    group: str
    extreme: bool


@dataclass(frozen=True)
class Sale:
    """One sale of one line. `category` is a top-level seed category, 1 to 10."""

    customer_id: str
    seconds_ago: int
    amount: Decimal
    store: int
    channel: int
    category: int

    def occurred_at(self, as_of: datetime) -> datetime:
        return as_of - timedelta(seconds=self.seconds_ago)


@dataclass(frozen=True)
class Dataset:
    seed: int
    customers: tuple[Customer, ...]
    sales: tuple[Sale, ...]

    def fingerprint(self) -> str:
        """Identifies these rows: the same seed and size always give this value."""
        digest = hashlib.sha256()
        for c in self.customers:
            digest.update(f"{c.customer_id}|{c.group}|{c.extreme}\n".encode())
        for s in self.sales:
            row = (
                f"{s.customer_id}|{s.seconds_ago}|{s.amount}|"
                f"{s.store}|{s.channel}|{s.category}\n"
            )
            digest.update(row.encode())
        return digest.hexdigest()


def _pick(rng: random.Random, weights: Sequence[float]) -> int:
    """A position in `weights`, by weight."""
    point = rng.random() * math.fsum(weights)
    running = 0.0
    for position, weight in enumerate(weights):
        running += weight
        if point < running:
            return position
    return len(weights) - 1


def _poisson(rng: random.Random, mean: float) -> int:
    """Knuth's method: the means here are small enough for it."""
    limit, count, product = math.exp(-mean), 0, rng.random()
    while product > limit:
        count += 1
        product *= rng.random()
    return count


def _normal(rng: random.Random) -> float:
    """Box-Muller. 1 - random() cannot be zero, so the logarithm is defined."""
    radius = math.sqrt(-2 * math.log(1 - rng.random()))
    return radius * math.cos(2 * math.pi * rng.random())


def _season_of(seconds_ago: int) -> float:
    moment = AS_OF - timedelta(seconds=seconds_ago)
    return SEASON[moment.month - 1]


def _seconds_ago(rng: random.Random, newest: float, oldest: float) -> int:
    """A moment between two day offsets, thinned by the season."""
    while True:
        days = newest + rng.random() * (oldest - newest)
        seconds = int(days * _SECONDS_PER_DAY)
        if rng.random() * max(SEASON) < _season_of(seconds):
            return seconds


def _sale(
    rng: random.Random,
    group: Group,
    customer_id: str,
    seconds_ago: int,
    home_store: int,
    extreme: bool,
) -> Sale:
    amount = group.ticket * math.exp(TICKET_SPREAD * _normal(rng))
    if extreme:
        amount *= EXTREME_FACTOR
    if rng.random() < HOME_STORE_SHARE:
        store = home_store
    else:
        store = 1 + int(rng.random() * STORES)
    return Sale(
        customer_id=customer_id,
        seconds_ago=seconds_ago,
        amount=max(Decimal("1.00"), Decimal(f"{amount:.2f}")),
        store=store,
        channel=1 + _pick(rng, group.channels),
        category=1 + _pick(rng, group.categories),
    )


def _group_sizes(customers: int) -> list[int]:
    """Each group's count by share. The last takes what rounding leaves."""
    sizes = [round(group.share * customers) for group in GROUPS[:-1]]
    return [*sizes, customers - sum(sizes)]


def generate(seed: int = DEFAULT_SEED, customers: int = DEFAULT_CUSTOMERS) -> Dataset:
    """The same seed and size always give the same rows."""
    rng = random.Random(seed)
    people: list[Customer] = []
    sales: list[Sale] = []
    position = 0
    for group, size in zip(GROUPS, _group_sizes(customers), strict=True):
        for _ in range(size):
            customer_id = str(uuid.uuid5(_NAMESPACE, f"{seed}-{position}"))
            position += 1
            extreme = rng.random() < EXTREME_SHARE
            people.append(Customer(customer_id, group.code, extreme))
            home_store = 1 + int(rng.random() * STORES)
            low, high = group.last_purchase_days
            last = low + rng.random() * (high - low)
            sales.append(
                _sale(
                    rng,
                    group,
                    customer_id,
                    int(last * _SECONDS_PER_DAY),
                    home_store,
                    extreme,
                )
            )
            oldest = max(last, min(group.tenure_days, HISTORY_DAYS))
            for _ in range(_poisson(rng, group.purchases)):
                seconds = _seconds_ago(rng, last, oldest)
                sales.append(
                    _sale(rng, group, customer_id, seconds, home_store, extreme)
                )
    return Dataset(seed, tuple(people), tuple(sales))


def rfm_rows(
    dataset: Dataset,
    window_days: int = DEFAULT_WINDOW_DAYS,
    as_of: datetime = AS_OF,
) -> list[RawRfm]:
    """R, F and M per customer inside a window, as the K-means code takes them.

    A customer with no sale in the window has no row, which is RN-21's unassigned.
    """
    limit = window_days * _SECONDS_PER_DAY
    newest: dict[str, int] = {}
    count: dict[str, int] = {}
    total: dict[str, Decimal] = {}
    for sale in dataset.sales:
        if sale.seconds_ago > limit:
            continue
        customer = sale.customer_id
        newest[customer] = min(newest.get(customer, limit), sale.seconds_ago)
        count[customer] = count.get(customer, 0) + 1
        total[customer] = total.get(customer, Decimal(0)) + sale.amount
    return [
        RawRfm(
            customer_id=customer,
            last_purchase_at=as_of - timedelta(seconds=newest[customer]),
            frequency=count[customer],
            monetary=total[customer],
        )
        for customer in sorted(newest)
    ]
