"""Segment migration between two runs (F7-04) — the classification itself.

ADR-0018 is explicit about the failure mode this exists to avoid: comparing
raw K-means cluster ids across two runs reports 100% migration on every run,
silently, because a cluster's arbitrary id has no meaning outside the fit
that produced it. The comparison here is on the stable label code only —
never on segment_id, and never on segmentation_run.method. Two runs from
different methods (RFM_RULES and KMEANS) compare cleanly because the label
vocabulary is the one contract both methods write to.

ADR-0003: the classification itself is pure — no SQL, no Connection. It
classifies over plain dicts a caller builds from web.db.segments.
list_run_labels and get_run_at, imported at module level like every other
service (web/services/audit.py, campaigns.py, segmentation.py, …). That
split is what lets the tests here pass plain dicts to classify_migration
instead of faking a cursor, while compute_migration's own SQL-reading
wiring is covered separately, by asserting on the statements the mocked
cursor received.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from psycopg import Connection

from web.db.segments import get_label_ordinals, get_run_at, list_run_labels


class MigrationCategory(Enum):
    """Every customer between two runs falls into exactly one of these."""

    UNCHANGED = "unchanged"
    MOVED = "moved"
    NEWLY_ASSIGNED = "newly_assigned"
    NEWLY_UNASSIGNED = "newly_unassigned"
    ABSENT_FROM_EARLIER = "absent_from_earlier"
    ABSENT_FROM_LATER = "absent_from_later"


class Direction(Enum):
    """Where a MOVED customer went, by segment_label.ordinal_position
    (ADR-0018's declared best-to-worst business order). Only meaningful for
    MOVED; every other category leaves this None."""

    IMPROVED = "improved"
    DECLINED = "declined"


@dataclass(frozen=True)
class CustomerMigration:
    """One customer's classification between an earlier and a later run.
    label_before/label_after are None exactly when the customer was
    unassigned in that run, or absent from it entirely — category
    disambiguates which. direction is set only when category is MOVED."""

    customer_id: Any
    label_before: str | None
    label_after: str | None
    category: MigrationCategory
    direction: Direction | None = None


class UnknownRun(ValueError):
    """One of the two run ids does not exist."""


def _classify(
    label_before: str | None,
    label_after: str | None,
    *,
    in_run_a: bool,
    in_run_b: bool,
    ordinals: dict[str, int],
) -> tuple[MigrationCategory, Direction | None]:
    """The classification rule, isolated as a pure function of two labels
    and two presence flags — no database, no run ids."""
    if not in_run_a:
        return MigrationCategory.ABSENT_FROM_EARLIER, None
    if not in_run_b:
        return MigrationCategory.ABSENT_FROM_LATER, None
    if label_before is None and label_after is None:
        return MigrationCategory.UNCHANGED, None
    if label_before is None:
        return MigrationCategory.NEWLY_ASSIGNED, None
    if label_after is None:
        return MigrationCategory.NEWLY_UNASSIGNED, None
    if label_before == label_after:
        return MigrationCategory.UNCHANGED, None

    # Lower ordinal_position is better (ADR-0018: declared best-to-worst).
    # label_code references segment_label (ON DELETE RESTRICT), and
    # get_label_ordinals reads all of segment_label, so a label missing
    # from ordinals means something other than a label code reached this
    # comparison -- e.g. a raw segment_id, the exact bug this guards
    # against. Fail loudly rather than report a move with no direction.
    try:
        before_rank = ordinals[label_before]
        after_rank = ordinals[label_after]
    except KeyError as exc:
        raise ValueError(f"Label {exc.args[0]!r} is not in the vocabulary.") from exc
    direction = Direction.IMPROVED if after_rank < before_rank else Direction.DECLINED
    return MigrationCategory.MOVED, direction


def classify_migration(
    labels_a: dict[Any, str | None],
    labels_b: dict[Any, str | None],
    ordinals: dict[str, int],
) -> list[CustomerMigration]:
    """Classify every customer present in either labels_a (earlier) or
    labels_b (later). Pure function — no database access, so it is the unit
    this module's tests exercise directly.

    A customer present in only one input is its own category rather than
    dropped, since a plain dict union already includes every key from both.
    """
    all_customers = set(labels_a) | set(labels_b)

    result = []
    for customer_id in sorted(all_customers, key=str):
        category, direction = _classify(
            labels_a.get(customer_id),
            labels_b.get(customer_id),
            in_run_a=customer_id in labels_a,
            in_run_b=customer_id in labels_b,
            ordinals=ordinals,
        )
        result.append(
            CustomerMigration(
                customer_id=customer_id,
                label_before=labels_a.get(customer_id),
                label_after=labels_b.get(customer_id),
                category=category,
                direction=direction,
            )
        )
    return result


def order_runs(
    connection: Connection[Any], run_id_a: int, run_id_b: int
) -> tuple[int, int]:
    """Return (earlier_id, later_id) by each run's own run_at, never by the
    order the caller happened to pass them in, and never by method.

    Refuses an unknown run id (UnknownRun) rather than silently returning a
    misleading result (ADR-0018's "plausible, no error" failure mode).
    """
    run_at_a = get_run_at(connection, run_id_a)
    run_at_b = get_run_at(connection, run_id_b)
    if run_at_a is None:
        raise UnknownRun(f"Run {run_id_a} does not exist.")
    if run_at_b is None:
        raise UnknownRun(f"Run {run_id_b} does not exist.")

    return (run_id_a, run_id_b) if run_at_a <= run_at_b else (run_id_b, run_id_a)


def compute_migration(
    connection: Connection[Any], run_id_a: int, run_id_b: int
) -> list[CustomerMigration]:
    """Read both runs and classify every customer between them.

    The pair is reordered by run_at (order_runs), so passing (later,
    earlier) gives the same result as (earlier, later).
    """
    earlier_id, later_id = order_runs(connection, run_id_a, run_id_b)

    labels_earlier = list_run_labels(connection, earlier_id)
    labels_later = list_run_labels(connection, later_id)
    ordinals = get_label_ordinals(connection)

    return classify_migration(labels_earlier, labels_later, ordinals)


_UNASSIGNED = "Unassigned"
_NOT_IN_EARLIER = "Not in earlier run"
_NOT_IN_LATER = "Not in later run"


@dataclass(frozen=True)
class MigrationMatrix:
    """A cross-tabulation of every customer's earlier label (row) against
    their later label (column) — AC 1. row_labels and column_labels share
    the same ordered vocabulary (best-to-worst, ADR-0018's ordinal_position)
    with an "Unassigned" row/column appended, so unassigned customers get
    their own row and column rather than being folded into an existing
    label (AC 3). A last "Not in earlier run" row and "Not in later run"
    column hold customers only one of the two runs scored, so no customer
    is dropped from the grid.

    cells[row_label][column_label] is the customer count for that pair.
    row_totals and column_totals are provided so a caller can check they
    reconcile with each run's own assigned/unassigned counts (AC 2) without
    re-summing the matrix itself.
    """

    row_labels: list[str]
    column_labels: list[str]
    cells: dict[str, dict[str, int]]
    row_totals: dict[str, int]
    column_totals: dict[str, int]
    # The same migrations used to produce ``cells`` grouped by cell.  Keeping
    # the members alongside the counts prevents a detail view from running a
    # second classification (and accidentally disagreeing with the matrix).
    cell_migrations: dict[str, dict[str, tuple[CustomerMigration, ...]]] = field(
        default_factory=dict
    )


def migration_cell_labels(migration: CustomerMigration) -> tuple[str, str]:
    """Return the matrix row and column for one already-classified customer.

    ``None`` labels mean an unassigned result, except for the two explicit
    absence categories.  Keeping this mapping in the service makes filters
    and detail rows use exactly the same states as the count matrix.
    """
    if migration.category is MigrationCategory.ABSENT_FROM_EARLIER:
        row = _NOT_IN_EARLIER
    elif migration.label_before is None:
        row = _UNASSIGNED
    else:
        row = migration.label_before

    if migration.category is MigrationCategory.ABSENT_FROM_LATER:
        column = _NOT_IN_LATER
    elif migration.label_after is None:
        column = _UNASSIGNED
    else:
        column = migration.label_after
    return row, column


def build_migration_matrix(
    migrations: list[CustomerMigration], ordinals: dict[str, int]
) -> MigrationMatrix:
    """Cross-tabulate a list of CustomerMigration into rows (earlier label)
    by columns (later label), counting customers per cell.

    label_before/label_after of None — an unassigned result, not an
    absence — map to the Unassigned row or column. A customer absent from
    the earlier run lands in the "Not in earlier run" row; one absent from
    the later run, in the "Not in later run" column. That is what makes
    AC 2 hold even when the two runs scored different customers: every
    row but "Not in earlier run" sums to the earlier run's count for that
    label, and every column but "Not in later run" to the later run's.

    Labels are ordered best-to-worst by ordinal_position, exactly the order
    segment_label declares (ADR-0018), then Unassigned since it isn't part
    of that vocabulary and has no rank to sort by, then the absence row or
    column.
    """
    ordered_labels = [
        label for label, _ in sorted(ordinals.items(), key=lambda item: item[1])
    ]
    row_labels = ordered_labels + [_UNASSIGNED, _NOT_IN_EARLIER]
    column_labels = ordered_labels + [_UNASSIGNED, _NOT_IN_LATER]

    cells: dict[str, dict[str, int]] = {
        row: {column: 0 for column in column_labels} for row in row_labels
    }
    cell_migrations: dict[str, dict[str, list[CustomerMigration]]] = {
        row: {column: [] for column in column_labels} for row in row_labels
    }

    for migration in migrations:
        row, column = migration_cell_labels(migration)
        cells[row][column] += 1
        cell_migrations[row][column].append(migration)

    row_totals = {row: sum(cells[row].values()) for row in row_labels}
    column_totals = {
        column: sum(cells[row][column] for row in row_labels)
        for column in column_labels
    }

    return MigrationMatrix(
        row_labels=row_labels,
        column_labels=column_labels,
        cells=cells,
        row_totals=row_totals,
        column_totals=column_totals,
        cell_migrations={
            row: {column: tuple(members) for column, members in columns.items()}
            for row, columns in cell_migrations.items()
        },
    )


@dataclass(frozen=True)
class ComponentDelta:
    """One RFM component's raw value and score in both runs, and the score
    delta between them. raw_before/raw_after and score_before/score_after
    are None exactly when that run left the customer unassigned -- RN-21 --
    so the caller can say "no scores exist for that run" instead of
    printing a false zero."""

    name: str
    raw_before: Any
    raw_after: Any
    score_before: int | None
    score_after: int | None

    @property
    def score_delta(self) -> int | None:
        if self.score_before is None or self.score_after is None:
            return None
        return self.score_after - self.score_before


@dataclass(frozen=True)
class MigrationExplanation:
    """Why one customer moved (or didn't) between two runs, entirely from
    the stored R/F/M values and scores on each run's history row -- no
    recomputation (ADR-0017). recency/frequency/monetary are the three
    ComponentDelta. most_changed names every component sharing the largest
    absolute score_delta -- one when a single component moved most, more
    when two or three tie (#297: a tie must not be reported as if only one
    of them moved). It is None when either run left the customer
    unassigned, since a score_delta of None can't be compared to the other
    two, or when nothing moved at all."""

    recency: ComponentDelta
    frequency: ComponentDelta
    monetary: ComponentDelta
    label_before: str | None
    label_after: str | None
    label_changed: bool
    # Whether the customer was part of each run at all. A customer absent
    # from the earlier run is new, which is not the same as having been
    # scored and left unassigned (RN-21), though both give label_before None
    # (#338).
    in_earlier: bool = True
    in_later: bool = True

    @property
    def most_changed(self) -> tuple[ComponentDelta, ...] | None:
        deltas = [self.recency, self.frequency, self.monetary]
        if any(delta.score_delta is None for delta in deltas):
            return None
        largest = max(abs(delta.score_delta) for delta in deltas)
        if largest == 0:
            return None
        return tuple(delta for delta in deltas if abs(delta.score_delta) == largest)

    @property
    def most_changed_caption(self) -> str | None:
        """The sentence the page shows: the one component that moved the
        most, or, on a tie, every component that shares it named as having
        moved equally (#297) rather than picking one by list order."""
        changed = self.most_changed
        if changed is None:
            return None
        if len(changed) == 1:
            component = changed[0]
            return (
                f"{component.name} moved the most "
                f"(score delta {component.score_delta:+d})."
            )
        names = ", ".join(c.name for c in changed[:-1]) + f" and {changed[-1].name}"
        deltas = ", ".join(f"{c.score_delta:+d}" for c in changed)
        return f"{names} moved equally (score deltas {deltas})."


def explain_migration(
    assignment_before: Any, assignment_after: Any
) -> MigrationExplanation:
    """Build the explanation from two RunAssignment-shaped objects (or None,
    when the customer was not part of that run at all) -- the two rows
    web.db.segments.get_customer_assignment_for_run reads, one per run.

    Pure function: everything it needs is already on the stored assignment,
    per ADR-0017 -- it reads r_score/f_score/m_score and the matching raw
    columns, and does not touch transaction or transaction_line to
    recompute anything.
    """

    def _component(name: str, raw_attr: str, score_attr: str) -> ComponentDelta:
        return ComponentDelta(
            name=name,
            raw_before=(
                getattr(assignment_before, raw_attr, None)
                if assignment_before
                else None
            ),
            raw_after=(
                getattr(assignment_after, raw_attr, None) if assignment_after else None
            ),
            score_before=(
                getattr(assignment_before, score_attr, None)
                if assignment_before
                else None
            ),
            score_after=(
                getattr(assignment_after, score_attr, None)
                if assignment_after
                else None
            ),
        )

    label_before = assignment_before.label_code if assignment_before else None
    label_after = assignment_after.label_code if assignment_after else None

    return MigrationExplanation(
        recency=_component("Recency", "recency_last_purchase_at", "r_score"),
        frequency=_component("Frequency", "frequency_count", "f_score"),
        monetary=_component("Monetary", "monetary_total", "m_score"),
        label_before=label_before,
        label_after=label_after,
        label_changed=label_before != label_after,
        in_earlier=assignment_before is not None,
        in_later=assignment_after is not None,
    )


# ---------- the explanation in plain language (#338, RN-50) ----------

# Whether a measure "changed" is judged on its raw value, never on its score.
# A quintile score is a rank among every customer the run measured (RFM_RULES
# scores with ntile), so it moves when other customers move, and a K-means run
# stores no scores at all (ADR-0018). The raw values are the customer's own
# behaviour and exist for both methods. RN-50 in docs/business-rules.md records
# each threshold and why it is that number.

# Recency grows by itself between runs when a customer does not buy -- a day
# per day -- so a week of drift is still the same behaviour.
RECENCY_STABLE_DAYS = 7
# A purchase count is small and whole; one purchase more or less is a change.
FREQUENCY_STABLE_PURCHASES = 0
# Spend is noisy; a relative band scales with how much the customer spends.
MONETARY_STABLE_RATIO = Decimal("0.10")

_RANK_ONLY = (
    "other customers moved the quintile cut points, not this customer's behaviour"
)


class Judgement(Enum):
    CHANGED = "changed"
    STABLE = "stable"
    NOT_MEASURED = "not measured"


@dataclass(frozen=True)
class MeasureSentence:
    """One measure between two runs, as a sentence a reader can act on.

    `phrase` is the short lower-case form the one-line summary joins.
    `rank_only` is True when the raw value did not change but the score
    did, which is a statement about the other customers, not this one.
    """

    name: str
    judgement: Judgement
    text: str
    phrase: str
    rank_only: bool = False


@dataclass(frozen=True)
class MigrationNarrative:
    """The three sentences, the summary line, and whether the customer was
    new to the later run or missing from it."""

    sentences: tuple[MeasureSentence, ...]
    is_new: bool
    left: bool
    # The label changed while every measure the customer controls stayed
    # stable and at least one score moved: the move is the rank's (RN-50).
    moved_by_rank_only: bool = False
    # Recency in days as each run measured it, for a page's table of values.
    recency_days_before: int | None = None
    recency_days_after: int | None = None

    @property
    def summary(self) -> str:
        text = ", ".join(sentence.phrase for sentence in self.sentences)
        return text[:1].upper() + text[1:] + "."

    @property
    def threshold_note(self) -> str:
        """RN-50's thresholds, stated from the constants the judgement uses."""
        purchases = (
            "the same number of purchases"
            if FREQUENCY_STABLE_PURCHASES == 0
            else f"purchases within {FREQUENCY_STABLE_PURCHASES}"
        )
        return (
            "Changed or stable is judged on the customer's own values, not the "
            f"scores: recency within {RECENCY_STABLE_DAYS} days, {purchases}, "
            f"and spend within {MONETARY_STABLE_RATIO:.0%} are stable (RN-50)."
        )


def recency_days(last_purchase_at: datetime | None, run_at: datetime) -> int | None:
    """Whole days from the last purchase to the run that measured it, or None
    when the run found no purchase (an unassigned result, RN-21)."""
    if last_purchase_at is None:
        return None
    return (run_at - last_purchase_at).days


def _plural(count: int, singular: str) -> str:
    return f"{count} {singular if count == 1 else singular + 's'}"


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _missing(
    component: ComponentDelta, explanation: MigrationExplanation
) -> MeasureSentence | None:
    """The sentence for a measure one of the runs holds no value for, or
    None when both runs measured it."""
    if component.raw_before is not None and component.raw_after is not None:
        return None
    reasons = []
    if component.raw_before is None:
        reasons.append(
            "the customer was not part of the earlier run"
            if not explanation.in_earlier
            else "the earlier run found no purchase in its window"
        )
    if component.raw_after is None:
        reasons.append(
            "the customer is not part of the later run"
            if not explanation.in_later
            else "the later run found no purchase in its window"
        )
    return MeasureSentence(
        name=component.name,
        judgement=Judgement.NOT_MEASURED,
        text=f"{component.name} cannot be compared: {' and '.join(reasons)}.",
        phrase=f"{component.name.lower()} not compared",
    )


def _score_note(component: ComponentDelta, rank_only: bool) -> str:
    delta = component.score_delta
    if not delta:
        return ""
    if rank_only:
        return (
            f" Its score still went from {component.score_before} to "
            f"{component.score_after}: {_RANK_ONLY}."
        )
    return f" Its score went from {component.score_before} to {component.score_after}."


def _recency(
    component: ComponentDelta,
    explanation: MigrationExplanation,
    run_at_before: datetime,
    run_at_after: datetime,
) -> MeasureSentence:
    if missing := _missing(component, explanation):
        return missing
    before = recency_days(component.raw_before, run_at_before)
    after = recency_days(component.raw_after, run_at_after)
    stable = abs(after - before) <= RECENCY_STABLE_DAYS
    same_purchase = component.raw_before == component.raw_after
    rank_only = stable and same_purchase and bool(component.score_delta)

    if before == after:
        text = f"Recency stayed at {_plural(after, 'day')} — stable."
    elif stable:
        text = (
            f"Recency went from {before} to {_plural(after, 'day')} — stable "
            f"(within {RECENCY_STABLE_DAYS} days)."
        )
    else:
        cause = (
            "longer since the last purchase"
            if after > before
            else "a more recent purchase"
        )
        text = (
            f"Recency went from {before} to {_plural(after, 'day')} — "
            f"changed: {cause}."
        )
    return MeasureSentence(
        name=component.name,
        judgement=Judgement.STABLE if stable else Judgement.CHANGED,
        text=text + (_score_note(component, rank_only) if stable else ""),
        phrase=(
            "recency stable"
            if stable
            else f"recency went from {before} to {_plural(after, 'day')}"
        ),
        rank_only=rank_only,
    )


def _frequency(
    component: ComponentDelta, explanation: MigrationExplanation
) -> MeasureSentence:
    if missing := _missing(component, explanation):
        return missing
    before, after = component.raw_before, component.raw_after
    stable = abs(after - before) <= FREQUENCY_STABLE_PURCHASES
    rank_only = before == after and bool(component.score_delta)

    if before == after:
        text = f"Frequency stayed at {_plural(after, 'purchase')} — stable."
    elif stable:
        text = f"Frequency went from {before} to {_plural(after, 'purchase')} — stable."
    else:
        verb = "rose" if after > before else "dropped"
        text = (
            f"Frequency {verb} from {before} to {_plural(after, 'purchase')} — changed."
        )
    return MeasureSentence(
        name=component.name,
        judgement=Judgement.STABLE if stable else Judgement.CHANGED,
        text=text + (_score_note(component, rank_only) if stable else ""),
        phrase=(
            "frequency stable"
            if stable
            else f"frequency {'rose' if after > before else 'dropped'} "
            f"from {before} to {after}"
        ),
        rank_only=rank_only,
    )


def _monetary(
    component: ComponentDelta, explanation: MigrationExplanation
) -> MeasureSentence:
    if missing := _missing(component, explanation):
        return missing
    before, after = component.raw_before, component.raw_after
    if before == 0:
        stable = after == 0
    else:
        stable = abs(after - before) / before <= MONETARY_STABLE_RATIO
    rank_only = before == after and bool(component.score_delta)
    percent = f"{MONETARY_STABLE_RATIO:.0%}"

    if before == after:
        text = f"Monetary stayed at {_money(after)} MXN — stable."
    elif stable:
        text = (
            f"Monetary went from {_money(before)} to {_money(after)} MXN — stable "
            f"(within {percent})."
        )
    else:
        verb = "rose" if after > before else "fell"
        text = (
            f"Monetary {verb} from {_money(before)} to {_money(after)} MXN — "
            f"changed (more than {percent})."
        )
    return MeasureSentence(
        name=component.name,
        judgement=Judgement.STABLE if stable else Judgement.CHANGED,
        text=text + (_score_note(component, rank_only) if stable else ""),
        phrase=(
            "monetary stable"
            if stable
            else f"monetary {'rose' if after > before else 'fell'} "
            f"from {_money(before)} to {_money(after)} MXN"
        ),
        rank_only=rank_only,
    )


def describe_migration(
    explanation: MigrationExplanation,
    *,
    run_at_before: datetime,
    run_at_after: datetime,
) -> MigrationNarrative:
    """The explanation in plain language (#338): one sentence per measure
    with its raw values -- recency in days, measured against each run's own
    `run_at` -- and a changed/stable judgement by RN-50's thresholds.

    Pure, like `explain_migration`: the run timestamps are passed in rather
    than read, so every threshold edge is testable without a database.
    """
    sentences = (
        _recency(explanation.recency, explanation, run_at_before, run_at_after),
        _frequency(explanation.frequency, explanation),
        _monetary(explanation.monetary, explanation),
    )
    return MigrationNarrative(
        sentences=sentences,
        moved_by_rank_only=(
            explanation.label_changed
            and all(s.judgement is Judgement.STABLE for s in sentences)
            and any(s.rank_only for s in sentences)
        ),
        is_new=not explanation.in_earlier,
        left=not explanation.in_later,
        recency_days_before=recency_days(explanation.recency.raw_before, run_at_before),
        recency_days_after=recency_days(explanation.recency.raw_after, run_at_after),
    )
