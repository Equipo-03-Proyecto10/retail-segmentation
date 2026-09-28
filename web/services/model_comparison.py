"""Comparing what two segmentation runs produced over the same customers (F9-04).

The comparison is made on **label codes** and on nothing else (ADR-0018). A run, for
this module, is its rows: each customer's name and the label the run gave them, or
None where it left them unassigned. There is no other input. How a run was produced
is never passed in and never read, so an assignment cannot be interpreted through it,
and the same labelled rows give the same comparison whatever produced them.

What it reports, decided here because a comparison that quietly meant something else
would look plausible and be wrong:

* **Population per label**, for each run, in the vocabulary's own best-to-worst order
  and then *Unassigned*. A label nobody holds is listed with zero.
* **Agreement per customer.** A customer both runs scored *agrees* when they were
  given the same label and *disagrees* otherwise. Two runs that both left a customer
  unassigned agree: each found nothing to label, which is a result and not an
  absence. One that left them unassigned and one that labelled them disagree.
* **A customer only one run scored** is reported as that, and is neither an
  agreement nor a disagreement: there is nothing to compare them with. The counts
  reconcile: everyone either run scored is agreed, disagreed or in only one.
* **A cross-tabulation** of the customers both runs scored, by the label each gave.
  Its cells sum to those customers and its diagonal to the agreements.

It is not a migration. Migration is a change over time and reads "improved" and
"declined"; two runs compared side by side have no direction, so nothing here says
one is better.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

UNASSIGNED = "Unassigned"

FILTERS = ("all", "agree", "disagree", "one_run")


class Agreement(Enum):
    AGREE = "agree"
    DISAGREE = "disagree"
    ONLY_IN_FIRST = "only_in_first"
    ONLY_IN_SECOND = "only_in_second"


@dataclass(frozen=True)
class LabelPopulation:
    """How many customers each run put under one label. `label` is None for the
    unassigned result. A share is None for a run with no customers."""

    label: str | None
    first: int
    second: int
    share_first: float | None
    share_second: float | None


@dataclass(frozen=True)
class CustomerComparison:
    customer_id: str
    name: str
    first: str | None
    second: str | None
    status: Agreement


@dataclass(frozen=True)
class Comparison:
    first_run_id: int
    second_run_id: int
    populations: tuple[LabelPopulation, ...]
    first_total: int
    second_total: int
    column_labels: tuple[str, ...]
    cells: dict[str, dict[str, int]]
    customers: tuple[CustomerComparison, ...]
    compared: int
    agree: int
    disagree: int
    only_first: int
    only_second: int
    agreement_rate: float | None


Rows = Iterable[tuple[str, str, str | None]]


def _index(
    rows: Rows, ordinals: Mapping[str, int]
) -> dict[str, tuple[str, str | None]]:
    indexed: dict[str, tuple[str, str | None]] = {}
    for customer_id, name, label in rows:
        if label is not None and label not in ordinals:
            raise ValueError(f"Label {label!r} is not in the vocabulary.")
        if customer_id in indexed:
            raise ValueError(f"Customer {customer_id} appears twice in one run.")
        indexed[customer_id] = (name, label)
    return indexed


def _share(count: int, total: int) -> float | None:
    return count / total if total else None


def compare_runs(
    first_rows: Rows,
    second_rows: Rows,
    ordinals: Mapping[str, int],
    *,
    first_run_id: int,
    second_run_id: int,
) -> Comparison:
    """Compare two runs' labelled rows against the label vocabulary.

    `ordinals` maps each label to its position in the vocabulary, 1 the best.
    Raises ValueError for a label outside it, or a customer twice in one run.
    """
    vocabulary = sorted(ordinals, key=ordinals.__getitem__)
    first = _index(first_rows, ordinals)
    second = _index(second_rows, ordinals)

    populations = []
    for label in [*vocabulary, None]:
        in_first = sum(1 for _, held in first.values() if held == label)
        in_second = sum(1 for _, held in second.values() if held == label)
        populations.append(
            LabelPopulation(
                label,
                in_first,
                in_second,
                _share(in_first, len(first)),
                _share(in_second, len(second)),
            )
        )

    columns = (*vocabulary, UNASSIGNED)
    cells = {row: dict.fromkeys(columns, 0) for row in columns}
    customers: list[CustomerComparison] = []
    agree = disagree = only_first = only_second = 0

    for customer_id in set(first) | set(second):
        if customer_id in first and customer_id in second:
            name, label_first = first[customer_id]
            label_second = second[customer_id][1]
            cells[label_first or UNASSIGNED][label_second or UNASSIGNED] += 1
            if label_first == label_second:
                status = Agreement.AGREE
                agree += 1
            else:
                status = Agreement.DISAGREE
                disagree += 1
        elif customer_id in first:
            name, label_first = first[customer_id]
            label_second, status = None, Agreement.ONLY_IN_FIRST
            only_first += 1
        else:
            name, label_second = second[customer_id]
            label_first, status = None, Agreement.ONLY_IN_SECOND
            only_second += 1
        customers.append(
            CustomerComparison(customer_id, name, label_first, label_second, status)
        )

    customers.sort(key=lambda customer: (customer.name, customer.customer_id))
    compared = agree + disagree
    return Comparison(
        first_run_id=first_run_id,
        second_run_id=second_run_id,
        populations=tuple(populations),
        first_total=len(first),
        second_total=len(second),
        column_labels=columns,
        cells=cells,
        customers=tuple(customers),
        compared=compared,
        agree=agree,
        disagree=disagree,
        only_first=only_first,
        only_second=only_second,
        agreement_rate=agree / compared if compared else None,
    )


def filter_customers(
    comparison: Comparison, status: str
) -> tuple[CustomerComparison, ...]:
    """The customers to list: all, only agreements, only disagreements, or only
    those a single run scored. Raises ValueError for anything else."""
    if status not in FILTERS:
        raise ValueError(f"Filter must be one of {', '.join(FILTERS)}.")
    wanted = {
        "all": set(Agreement),
        "agree": {Agreement.AGREE},
        "disagree": {Agreement.DISAGREE},
        "one_run": {Agreement.ONLY_IN_FIRST, Agreement.ONLY_IN_SECOND},
    }[status]
    return tuple(c for c in comparison.customers if c.status in wanted)


def _text(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, list | tuple):
        return ", ".join(_text(item) for item in value)
    return str(value)


def flatten_parameters(
    parameters: Mapping[str, Any], prefix: str = ""
) -> list[tuple[str, str]]:
    """A run's recorded parameters as (name, text) pairs, in the order they were
    recorded, nested values named by their path. It shows what was recorded and
    interprets none of it."""
    flat: list[tuple[str, str]] = []
    for key, value in parameters.items():
        name = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.extend(flatten_parameters(value, f"{name}."))
        else:
            flat.append((name, _text(value)))
    return flat
