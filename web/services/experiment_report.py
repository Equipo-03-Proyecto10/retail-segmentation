"""The campaign and experiment report and its export (F12-04, ADR-0019).

Assignment, exposure and conversion are three counts with three denominators
and are shown as such. The uplift beside them is the intent-to-treat figure of
F11-07, over every assigned customer, with its 95% interval; it is computed by
that service's own functions, not a second copy.

Provenance survives everything: a report row and an exported line both carry
the experiment's data origin, and `Synthetic` for seeded or injected data.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime

from psycopg import Connection

from web.db import experiment_conversions as conversions
from web.db import experiment_report as report_db
from web.db.experiment_report import ReportGroup
from web.db.experiments import Experiment
from web.services import experiment_uplift as uplift
from web.services.experiments import DATA_ORIGINS, is_synthetic
from web.services.pagination import page_count

PAGE_SIZE = 10
# The most experiments one export carries; far above any real campaign count.
EXPORT_LIMIT = 10_000

EXPORT_COLUMNS = (
    "experiment_id",
    "experiment",
    "campaign_id",
    "data_origin",
    "label",
    "arm",
    "group_id",
    "assigned",
    "exposed",
    "converted",
    "pending",
    "not_converted",
    "uplift_points",
    "ci_low_points",
    "ci_high_points",
    "p_value",
    "measurement",
)


class InvalidFilter(ValueError):
    """A filter value that is not one the report offers."""


@dataclass(frozen=True)
class ExperimentReport:
    experiment: Experiment
    groups: tuple[ReportGroup, ...]
    arms: tuple[uplift.ArmResult, ...]
    refusal: str | None

    @property
    def label(self) -> str | None:
        return (
            uplift.SYNTHETIC_LABEL
            if is_synthetic(self.experiment.data_origin)
            else None
        )

    @property
    def pending(self) -> int:
        return sum(g.pending for g in self.groups)

    def uplift_of(self, group_id: int) -> uplift.Comparison | None:
        return next((a.comparison for a in self.arms if a.group_id == group_id), None)


@dataclass(frozen=True)
class ReportPage:
    rows: tuple[ExperimentReport, ...]
    total: int
    page: int
    page_count: int

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.page_count


def parse_filters(
    campaign: str, origin: str, offered_campaign_ids: Collection[int]
) -> tuple[int | None, str | None]:
    """Read the two optional filters, refusing a value that is not one of the
    report's rather than dropping it (RN-45)."""
    campaign_id: int | None = None
    if campaign:
        if not (campaign.isascii() and campaign.isdigit()) or int(campaign) > 2**31 - 1:
            raise InvalidFilter("Choose a campaign from the list.")
        campaign_id = int(campaign)
        if campaign_id not in offered_campaign_ids:
            raise InvalidFilter("Choose a campaign from the list.")
    if origin and origin not in DATA_ORIGINS:
        raise InvalidFilter("Choose a data origin from the list.")
    return campaign_id, origin or None


def _report_of(experiment: Experiment, groups: list[ReportGroup]) -> ExperimentReport:
    mine = tuple(g for g in groups if g.experiment_id == experiment.experiment_id)
    reason = uplift.refusal_before_counts(
        experiment.experiment_id,
        experiment.control_groups,
        experiment.target_metric,
        experiment.assignments,
    )
    arms: tuple[uplift.ArmResult, ...] = ()
    if reason is None:
        try:
            _, arms = uplift.compare_arms(
                experiment.experiment_id,
                [
                    conversions.GroupConversion(
                        g.group_id,
                        g.kind,
                        g.assigned,
                        g.converted,
                        g.pending,
                        g.not_converted,
                        g.unrecorded,
                    )
                    for g in mine
                ],
            )
        except uplift.UpliftRefused as refusal:
            reason = str(refusal)
    return ExperimentReport(experiment, mine, arms, reason)


def _build(
    connection: Connection,
    campaign_id: int | None,
    data_origin: str | None,
    limit: int,
    offset: int,
    now: datetime,
) -> tuple[tuple[ExperimentReport, ...], int]:
    found, total = report_db.list_report_experiments(
        connection,
        campaign_id=campaign_id,
        data_origin=data_origin,
        limit=limit,
        offset=offset,
    )
    groups = report_db.list_report_groups(
        connection, [e.experiment_id for e in found], now
    )
    return tuple(_report_of(e, groups) for e in found), total


def build_report(
    connection: Connection,
    *,
    campaign_id: int | None,
    data_origin: str | None,
    page: int,
    now: datetime | None = None,
) -> ReportPage:
    moment = now or datetime.now(UTC)
    rows, total = _build(
        connection, campaign_id, data_origin, PAGE_SIZE, (page - 1) * PAGE_SIZE, moment
    )
    return ReportPage(rows, total, page, page_count(total, PAGE_SIZE))


def _safe(value: object) -> object:
    """Stop a spreadsheet reading a text cell as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def export_csv(
    connection: Connection,
    *,
    campaign_id: int | None,
    data_origin: str | None,
    now: datetime | None = None,
) -> str:
    """Every experiment matching the filters, one line per group. Each line
    carries the data origin and its `Synthetic` label, so the provenance is in
    the file and not only on the screen it came from."""
    moment = now or datetime.now(UTC)
    reports, _ = _build(connection, campaign_id, data_origin, EXPORT_LIMIT, 0, moment)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(EXPORT_COLUMNS)
    for report in reports:
        e = report.experiment
        measurement = report.refusal or (
            "Intent to treat over all assigned customers, 95% interval"
            + (", preliminary: windows still open" if report.pending else "")
        )
        for g in report.groups:
            c = report.uplift_of(g.group_id)
            writer.writerow(
                [
                    _safe(v)
                    for v in (
                        e.experiment_id,
                        e.name,
                        e.campaign_id if e.campaign_id is not None else "",
                        e.data_origin,
                        report.label or "",
                        g.kind,
                        g.group_id,
                        g.assigned,
                        g.exposed,
                        g.converted,
                        g.pending,
                        g.not_converted,
                        f"{c.uplift * 100:.2f}" if c else "",
                        f"{c.ci_low * 100:.2f}" if c else "",
                        f"{c.ci_high * 100:.2f}" if c else "",
                        f"{c.p_value:.4f}" if c else "",
                        measurement,
                    )
                ]
            )
    return out.getvalue()
