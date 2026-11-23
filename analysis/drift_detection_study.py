"""Planted drift: what the monitor detects, and how often it alarms on nothing
(F23-10, #822).

    python -m analysis.drift_detection_study

Regenerates every table in `docs/evidence/f23-10-drift-detection.md`. It writes its
reports and alerts through model-registry to a throwaway database loaded from the
three SQL scripts; the tables go to standard output, and nothing here is deployed or
imported by anything that runs.

**The monitor is the production one.** Each window is fed to model-registry's own
`monitor.run_once`, which judges it and writes `models.drift_report`,
`drift_variable`, `drift_statistic`, `drift_bin` and `drift_alert`; the study reads
those rows back. The profiles it feeds are built by rfm-engine's own `app/profile.py`,
which is what fills `current_profile_v1`, and the reference histograms by
model-registry's own `training._histogram`. Those modules are loaded from their
files; the monitor's orchestration, persistence rule and alert decision are not
copied.

**The version.** An RFM_RULES version frozen on the evaluation dataset (F23-01, seed
20261031): its quintile boundaries are that population's, by the production query's
distinct-value ntile (#352, as F23-06 replicates it), its bands are the seed's
(RN-46), and its reference is the label shares and the R, F and M deciles of that
population's profiles at the dataset's instant. A window is every scored customer at
its end, as ADR-0088 reads it.

**The windows.** A window without a shift is a fresh draw of the same generator at a
seed of its own: the same distribution, other customers. That is ADR-0065's (6), "a
single fixed distribution", and it is stricter than resampling the reference's own
customers, which would sit closer to the reference than any new month does. Two
draws in a row are the two consecutive windows of the persistence rule, so an alert
is a second window that alarms after a first one did.

**The planted shifts.** Applied to the sales of a window before it is scored, each
at stated sizes (`SHIFTS`), on the same draws as the no-shift windows, so a
difference in the figures is the shift and not the draw:

- `SPEND`: every sale's amount times (1 + size).
- `FREQUENCY`: each sale except the customer's newest removed with probability size,
  so purchases fall and recency does not move.
- `CATEGORY`: each sale moved to category `PLANTED_CATEGORY` with probability size.
- `CHANNEL`: each sale moved to channel `PLANTED_CHANNEL` with probability size.

**What the monitor cannot see.** ADR-0088 decided that categories and channels are
not variables: the monitor judges segment shares, R, F and M only. A category or
channel shift leaves every customer's R, F and M as they were, so its windows are the
no-shift windows exactly, and its detection rate is the false-alarm rate by
construction. The study runs them anyway and reports that, because the story asks for
the figure and the honest figure is that this monitor is blind to them.

**The bar for false alarms.** ADR-0065 sets no rate as such; it says the rate is
measured, not assumed. The one rate it does state is the chi-square alarm level,
p < 0.01. This study holds the false-alarm rate to that 1% (`FALSE_ALARM_LIMIT`), per
window and per alert, and reports the one-sided 95% upper bound beside each rate so
"within" is not read off a lucky sample. That reading is this study's, and the owner
of ADR-0065 may set another.

**Consecutive windows.** In production two consecutive windows share 29 of their 30
days, so they are not independent (ADR-0088). Independent draws give the alert rate
for independent windows. When consecutive windows are strongly dependent, the alert
rate approaches the single-window alarm rate, so that rate is reported too, as the
bound that holds either way.

The same seeds always give the same figures, and `report()` ends with their SHA-256.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import random
import statistics
import sys
import types
from argparse import ArgumentParser
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg

from analysis.dataset import (
    AS_OF,
    DEFAULT_CUSTOMERS,
    DEFAULT_SEED,
    DEFAULT_WINDOW_DAYS,
    Dataset,
    Sale,
    generate,
)
from analysis.k_study import VOCABULARY
from analysis.load import is_local
from analysis.method_comparison import _cut, rules
from analysis.normalization_study import table

_ROOT = Path(__file__).resolve().parents[1]
_DAY = 86400
_CENT = Decimal("0.01")

STUDY_SEED = 822
WINDOWS = 400  # no-shift windows, so 200 consecutive pairs
DETECTION_PAIRS = 50  # consecutive pairs per planted shift
PLANTED_CATEGORY = 1
PLANTED_CHANNEL = 1
SHIFTS: dict[str, tuple[float, ...]] = {
    "SPEND": (0.10, 0.25, 0.50, 1.00),
    "FREQUENCY": (0.10, 0.25, 0.40, 0.50),
    "CATEGORY": (0.10, 0.25, 0.50),
    "CHANNEL": (0.10, 0.25, 0.50),
}
# The variable a shift acts on first, and which way its values move. An alert
# explains a planted change when it is on that variable and its bins moved that way.
EXPECTED: dict[str, tuple[str, int]] = {
    "SPEND": ("M", +1),
    "FREQUENCY": ("F", -1),
}
CONFIDENCE = 0.95


# ---------- the production modules, loaded from their files ----------


def _service_module(service: str, name: str) -> types.ModuleType:
    """One module of a service's `app` package, imported without the package's
    `__init__`, which would start the web application. Each service gets a package
    name of its own, because every service calls its package `app`."""
    package = "_f23_10_" + service.replace("-", "_")
    if package not in sys.modules:
        module = types.ModuleType(package)
        module.__path__ = [str(_ROOT / "services" / service / "app")]
        sys.modules[package] = module
    # The root study environment intentionally does not install a service's private
    # package dependencies. monitor imports audit.py, whose drift path does not use
    # ApiError; supply only that unused import while loading the production modules.
    temporary_kit = False
    if service == "model-registry" and name == "monitor":
        try:
            importlib.import_module("mosaiq_kit")
        except ModuleNotFoundError:
            kit = types.ModuleType("mosaiq_kit")
            kit.ApiError = type("ApiError", (Exception,), {})
            sys.modules["mosaiq_kit"] = kit
            temporary_kit = True
    try:
        return importlib.import_module(f"{package}.{name}")
    finally:
        if temporary_kit:
            del sys.modules["mosaiq_kit"]


drift = _service_module("model-registry", "drift")
monitor = _service_module("model-registry", "monitor")
training = _service_module("model-registry", "training")
profile = _service_module("rfm-engine", "profile")

FALSE_ALARM_LIMIT = drift.P_ALARM  # the only rate ADR-0065 states: 1%


# ---------- the version: boundaries, bands and reference ----------


def _measures(sales: Sequence[Sale]) -> dict[str, Any]:
    """Each customer's measures over the version's window at `AS_OF`, as
    rfm-engine's `Measures` takes them. A customer with no sale in it is absent."""
    limit = DEFAULT_WINDOW_DAYS * _DAY
    held: dict[str, list[Any]] = {}
    for sale in sales:
        if sale.seconds_ago > limit:
            continue
        row = held.get(sale.customer_id)
        if row is None:
            held[sale.customer_id] = [
                1,
                sale.amount,
                sale.seconds_ago,
                sale.seconds_ago,
            ]
            continue
        row[0] += 1
        row[1] += sale.amount
        row[2] = max(row[2], sale.seconds_ago)
        row[3] = min(row[3], sale.seconds_ago)
    return {
        who: profile.Measures(
            count=row[0],
            total=row[1],
            first_at=AS_OF - timedelta(seconds=row[2]),
            last_at=AS_OF - timedelta(seconds=row[3]),
        )
        for who, row in sorted(held.items())
    }


def _floors(cut: list[tuple[int, Any]]) -> dict[int, Any]:
    floors = dict(cut)
    if sorted(floors) != [1, 2, 3, 4, 5]:
        raise ValueError("a quintile boundary needs five distinct values")
    return floors


def freeze(dataset: Dataset) -> Any:
    """The rule set an RFM_RULES version frozen on this population scores under:
    its quintile boundaries and the seed's bands."""
    measured = _measures(dataset.sales)
    r_days = [int((AS_OF - m.last_at) // timedelta(days=1)) for m in measured.values()]
    recency = _floors(_cut([-days for days in r_days]))
    frequency = _floors(_cut([m.count for m in measured.values()]))
    monetary = _floors(_cut([m.total for m in measured.values()]))
    return profile.RuleSet(
        rule_set_id=1,
        window_days=DEFAULT_WINDOW_DAYS,
        recency=tuple(-recency[score] for score in (5, 4, 3, 2)),
        frequency=tuple(frequency[score] for score in (5, 4, 3, 2)),
        monetary=tuple(monetary[score] for score in (5, 4, 3, 2)),
        bands=tuple(
            profile.Band(label, (b[0], b[1]), (b[2], b[3]), (b[4], b[5]))
            for _, b, label in rules()
        ),
        fallback_label=VOCABULARY[-1],
    )


def score(rule_set: Any, sales: Sequence[Sale]) -> list[Any]:
    """Every scored customer's profile at the window's end: what
    `current_profile_v1` holds and the monitor reads."""
    return [
        built
        for built in (
            profile.build(rule_set, measured, AS_OF)
            for measured in _measures(sales).values()
        )
        if built.state == "scored"
    ]


def reference_of(profiles: Sequence[Any]) -> dict[str, Any]:
    """The reference distribution of a version, in the shape the monitor reads:
    the label shares, and R, F and M by the registry's own histogram."""
    n = len(profiles)
    counts = Counter(p.label_code for p in profiles)
    columns = {
        "R": [float(p.r_days) for p in profiles],
        "F": [float(p.f_count) for p in profiles],
        "M": [float(p.m_total) for p in profiles],
    }
    return {
        "sample_size": n,
        "features": [
            {"feature": name, **training._histogram(values)}
            for name, values in columns.items()
        ],
        "labels": [
            {"label_code": label, "share": counts[label] / n}
            for label in VOCABULARY
            if counts[label]
        ],
    }


# ---------- the production monitor over a throwaway database ----------


@dataclass(frozen=True, slots=True)
class Observation:
    """One persisted monitor report, read back from its service-owned tables."""

    variables: tuple[Any, ...]
    alerts: frozenset[str]


def _role_url(owner_url: str, role: str) -> str:
    parts = urlsplit(owner_url)
    host = parts.hostname or "127.0.0.1"
    netloc = f"{role}:{role}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


def _experiment_start(
    reference: dict[str, Any], windows: int, detection_pairs: int, customers: int
) -> datetime:
    """A repeatable, isolated range of future windows for this exact experiment.

    The service keys a report by version and window end. Including the production
    sources in the key makes a rerun reuse reports only when both the inputs and the
    code under test are unchanged.
    """
    payload = {
        "reference": reference,
        "windows": windows,
        "detection_pairs": detection_pairs,
        "customers": customers,
        "shifts": SHIFTS,
        "study_seed": STUDY_SEED,
        "monitor": Path(monitor.__file__).read_text(),
        "drift": Path(drift.__file__).read_text(),
    }
    key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).digest()
    # 2,000,000 days leaves ample room below datetime.max for all condition blocks.
    offset = int.from_bytes(key[:8], "big") % 2_000_000
    return datetime(2200, 1, 1, 3, tzinfo=UTC) + timedelta(days=offset)


class MonitorStudy:
    """Feed profiles to `monitor.run_once`, then read what it persisted.

    Only session-local feed and version tables are created. The actual reports,
    variables, statistics, bins, alerts and audit events go through model-registry's
    production write path. The caller supplies a throwaway database loaded from the
    repository's three SQL scripts.
    """

    def __init__(self, database_url: str, reference: dict[str, Any]) -> None:
        if not database_url:
            raise ValueError("a throwaway database URL is required")
        if not is_local(database_url):
            raise ValueError("the drift study runs only against a local database")
        self.connection = psycopg.connect(
            _role_url(database_url, "svc_models"), autocommit=True
        )
        self.reference = reference
        self._originals: dict[str, str] = {}

    def __enter__(self) -> MonitorStudy:
        connection = self.connection
        connection.execute(
            "CREATE TEMP TABLE drift_study_version ("
            "version_id bigint, name text, reference_distribution jsonb)"
        )
        connection.execute(
            "INSERT INTO pg_temp.drift_study_version "
            "(version_id, name, reference_distribution) "
            "SELECT version_id, name, %s::jsonb FROM models.model_version "
            "WHERE state = 'PUBLISHED'",
            (json.dumps(self.reference),),
        )
        connection.execute(
            "CREATE TEMP TABLE drift_study_profile ("
            "label_code text, r_days int, f_count int, m_total numeric, "
            "computed_at timestamptz)"
        )
        replacements = {
            "_PUBLISHED": (
                "SELECT version_id, name, reference_distribution "
                "FROM pg_temp.drift_study_version"
            ),
            "_PROFILES": (
                "SELECT label_code, r_days, f_count, m_total, computed_at "
                "FROM pg_temp.drift_study_profile"
            ),
            # This experiment holds the version fixed; publication behavior has its
            # own monitor integration tests.
            "_VERSION_CHANGES": (
                "SELECT count(*) FROM models.model_publication "
                "WHERE published_at > %s AND false"
            ),
        }
        for name, value in replacements.items():
            self._originals[name] = getattr(monitor, name)
            setattr(monitor, name, value)
        return self

    def __exit__(self, *_error: object) -> None:
        for name, value in self._originals.items():
            setattr(monitor, name, value)
        self.connection.close()

    def window(self, profiles: Sequence[Any], end: datetime) -> Observation:
        """Run and read one real report at a scheduled window end."""
        connection = self.connection
        connection.execute("TRUNCATE pg_temp.drift_study_profile")
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO pg_temp.drift_study_profile "
                "(label_code, r_days, f_count, m_total, computed_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                [(p.label_code, p.r_days, p.f_count, p.m_total, end) for p in profiles],
            )
        report_id = monitor.run_once(connection, end)
        if report_id is None:
            raise RuntimeError("the throwaway database has no published model version")
        return self._read(report_id)

    def _read(self, report_id: int) -> Observation:
        connection = self.connection
        (customers,) = connection.execute(
            "SELECT customer_count FROM models.drift_report WHERE report_id = %s",
            (report_id,),
        ).fetchone()
        states = connection.execute(
            "SELECT variable, state FROM models.drift_variable "
            "WHERE report_id = %s ORDER BY "
            "CASE variable WHEN 'SEGMENT_SHARES' THEN 0 WHEN 'R' THEN 1 "
            "WHEN 'F' THEN 2 ELSE 3 END",
            (report_id,),
        ).fetchall()
        statistics_by: dict[str, list[Any]] = {}
        for variable, name, value, p_value, dof, level in connection.execute(
            "SELECT variable, statistic, value, p_value, degrees_of_freedom, level "
            "FROM models.drift_statistic WHERE report_id = %s "
            "ORDER BY variable, statistic DESC",
            (report_id,),
        ).fetchall():
            statistics_by.setdefault(variable, []).append(
                drift.Statistic(name, value, p_value, dof, level)
            )
        bins_by: dict[str, list[Any]] = {}
        for (
            variable,
            label,
            lower,
            upper,
            reference_share,
            current_share,
        ) in connection.execute(
            "SELECT variable, label, lower_bound, upper_bound, reference_share, "
            "current_share FROM models.drift_bin WHERE report_id = %s "
            "ORDER BY variable, position",
            (report_id,),
        ).fetchall():
            bins_by.setdefault(variable, []).append(
                drift.Bin(
                    label,
                    lower,
                    upper,
                    reference_share,
                    current_share,
                    round(current_share * customers),
                )
            )
        variables = tuple(
            drift.Variable(
                variable,
                state,
                tuple(statistics_by[variable]),
                tuple(bins_by[variable]),
            )
            for variable, state in states
        )
        alerts = frozenset(
            variable
            for (variable,) in connection.execute(
                "SELECT variable FROM models.drift_alert WHERE report_id = %s",
                (report_id,),
            ).fetchall()
        )
        return Observation(variables, alerts)


# ---------- readings from a persisted monitor window ----------


def direction(variable: Any) -> int:
    """Which way a feature's values moved: +1 toward its higher bins, -1 toward its
    lower, 0 when they did not. The bin for no value is not on the scale."""
    moved = sum(
        position * (item.current_share - item.reference_share)
        for position, item in enumerate(variable.bins)
        if item.label != drift.NO_VALUE
    )
    if abs(moved) < 1e-12:
        return 0
    return 1 if moved > 0 else -1


# ---------- windows and planted shifts ----------


def _int_seed(*parts: object) -> int:
    text = "|".join(str(part) for part in parts)
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")


def window_seed(index: int) -> int:
    """The generator seed of the no-shift window `index`: never the reference's."""
    return _int_seed("f23-10-window", STUDY_SEED, index)


def plant(dataset: Dataset, kind: str, size: float, index: int) -> list[Sale]:
    """The window's sales with one shift planted at one size. The random choices are
    a function of the study seed, the window, the kind and the size only."""
    rng = random.Random(_int_seed("f23-10-plant", STUDY_SEED, index, kind, size))
    sales = list(dataset.sales)
    if kind == "SPEND":
        factor = Decimal(str(1 + size))
        return [replace(s, amount=(s.amount * factor).quantize(_CENT)) for s in sales]
    if kind == "FREQUENCY":
        newest: dict[str, int] = {}
        for s in sales:
            newest[s.customer_id] = min(
                newest.get(s.customer_id, s.seconds_ago), s.seconds_ago
            )
        kept, spared = [], set()
        for s in sales:
            if s.seconds_ago == newest[s.customer_id] and s.customer_id not in spared:
                spared.add(s.customer_id)
                kept.append(s)
            elif rng.random() >= size:
                kept.append(s)
        return kept
    if kind == "CATEGORY":
        return [
            replace(s, category=PLANTED_CATEGORY) if rng.random() < size else s
            for s in sales
        ]
    if kind == "CHANNEL":
        return [
            replace(s, channel=PLANTED_CHANNEL) if rng.random() < size else s
            for s in sales
        ]
    raise ValueError(kind)


def planted_effect(kind: str, before: Sequence[Sale], after: Sequence[Sale]) -> float:
    """The planted change measured on the window's sales in the version's window:
    the change in total spend, in sales, or in the planted category's or channel's
    share of sales, as a fraction (spend, sales) or in share points."""
    limit = DEFAULT_WINDOW_DAYS * _DAY
    b = [s for s in before if s.seconds_ago <= limit]
    a = [s for s in after if s.seconds_ago <= limit]
    if kind == "SPEND":
        return float(sum(s.amount for s in a) / sum(s.amount for s in b) - 1)
    if kind == "FREQUENCY":
        return len(a) / len(b) - 1
    pick: Callable[[Sale], bool] = (
        (lambda s: s.category == PLANTED_CATEGORY)
        if kind == "CATEGORY"
        else (lambda s: s.channel == PLANTED_CHANNEL)
    )
    return sum(map(pick, a)) / len(a) - sum(map(pick, b)) / len(b)


# ---------- the experiment ----------


@dataclass
class Tally:
    """What one condition (no shift, or one shift at one size) gave."""

    windows: int = 0
    alarmed_windows: int = 0
    alarmed_by: Counter[str] = field(default_factory=Counter)
    pairs: int = 0
    alerted_pairs: int = 0
    alerted_by: Counter[str] = field(default_factory=Counter)
    explained: int = 0
    effects: list[float] = field(default_factory=list)
    psi: dict[str, list[float]] = field(default_factory=dict)
    example: dict[str, Any] | None = None

    def window(self, observation: Observation) -> None:
        self.windows += 1
        found = [
            v.variable for v in observation.variables if v.state in drift.PENDING_STATES
        ]
        if found:
            self.alarmed_windows += 1
            self.alarmed_by.update(found)
        for v in observation.variables:
            self.psi.setdefault(v.variable, []).append(v.statistics[0].value)


def _conditions() -> list[tuple[str, float]]:
    return [(kind, size) for kind, sizes in SHIFTS.items() for size in sizes]


def run(
    windows: int = WINDOWS,
    detection_pairs: int = DETECTION_PAIRS,
    customers: int = DEFAULT_CUSTOMERS,
    reference_seed: int = DEFAULT_SEED,
    database_url: str | None = None,
    monitor_type: type[MonitorStudy] = MonitorStudy,
) -> dict[str, Any]:
    """The whole experiment. The no-shift tally covers `windows` draws; each shift
    is planted on the first `2 * detection_pairs` of the same draws. Every draw is
    passed through `monitor.run_once` and read back from `models.drift_*`."""
    if windows % 2 or windows < 2 * detection_pairs:
        raise ValueError("windows must be even and hold every detection pair")
    database_url = database_url or os.environ.get("DRIFT_STUDY_DATABASE_URL")
    if not database_url:
        raise ValueError("DRIFT_STUDY_DATABASE_URL or --database-url is required")
    base = generate(reference_seed, customers)
    rule_set = freeze(base)
    reference = reference_of(score(rule_set, base.sales))
    tallies: dict[tuple[str, float], Tally] = {("NONE", 0.0): Tally()}
    for condition in _conditions():
        tallies[condition] = Tally()
    start = _experiment_start(reference, windows, detection_pairs, customers)
    stride = windows + 2
    positions = {
        condition: position
        for position, condition in enumerate([("NONE", 0.0), *_conditions()])
    }
    with monitor_type(database_url, reference) as monitored:
        for index in range(windows):
            dataset = generate(window_seed(index), customers)
            conditions = [("NONE", 0.0)]
            if index < 2 * detection_pairs:
                conditions += _conditions()
            for condition in conditions:
                kind, size = condition
                sales = (
                    list(dataset.sales)
                    if kind == "NONE"
                    else plant(dataset, kind, size, index)
                )
                tally = tallies[condition]
                if kind != "NONE":
                    tally.effects.append(planted_effect(kind, dataset.sales, sales))
                end = start + timedelta(days=positions[condition] * stride + index)
                observation = monitored.window(score(rule_set, sales), end)
                tally.window(observation)
                if index % 2 == 1:
                    tally.pairs += 1
                    if observation.alerts:
                        tally.alerted_pairs += 1
                        tally.alerted_by.update(observation.alerts)
                        if _explains(kind, observation):
                            tally.explained += 1
                        if tally.example is None:
                            tally.example = _example(kind, size, index, observation)
    return {
        "reference": reference,
        "rule_set": rule_set,
        "tallies": tallies,
        "windows": windows,
        "detection_pairs": detection_pairs,
        "customers": customers,
    }


def _explains(kind: str, observation: Observation) -> bool:
    """An alert explains the planted change when it is raised on the variable the
    shift acts on and that variable's values moved the planted way."""
    if kind not in EXPECTED:
        return False
    name, way = EXPECTED[kind]
    for v in observation.variables:
        if v.variable == name and name in observation.alerts:
            return direction(v) == way
    return False


def _example(kind: str, size: float, index: int, observation: Observation) -> dict:
    alerts = []
    for v in observation.variables:
        if v.variable in observation.alerts:
            alerts.append(
                {
                    "variable": v.variable,
                    "review_code": drift.review_code(v.variable),
                    "statistics": [
                        (s.statistic, round(s.value, 4), s.p_value, s.level)
                        for s in v.statistics
                    ],
                    "text": drift.review_text(v.variable, list(v.bins)),
                }
            )
    return {"kind": kind, "size": size, "window": index, "alerts": alerts}


# ---------- figures ----------


def upper_bound(successes: int, trials: int, confidence: float = CONFIDENCE) -> float:
    """The one-sided Clopper-Pearson upper bound of a rate: the largest rate under
    which so few successes still have probability 1 - confidence."""
    if trials == 0:
        return 1.0
    if successes >= trials:
        return 1.0
    alpha = 1 - confidence

    def cdf(p: float) -> float:
        return sum(
            math.comb(trials, k) * p**k * (1 - p) ** (trials - k)
            for k in range(successes + 1)
        )

    low, high = successes / trials, 1.0
    for _ in range(100):
        middle = (low + high) / 2
        if cdf(middle) > alpha:
            low = middle
        else:
            high = middle
    return high


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _rate(k: int, n: int) -> str:
    return f"{k}/{n} = {_pct(k / n)}" if n else "—"


def figures(result: dict[str, Any]) -> dict[str, Any]:
    """Every number the report states, as plain data: what the digest covers."""
    tallies: dict[tuple[str, float], Tally] = result["tallies"]
    none = tallies[("NONE", 0.0)]
    out: dict[str, Any] = {
        "reference": {
            "customers": result["reference"]["sample_size"],
            "labels": result["reference"]["labels"],
        },
        "false_alarms": {
            "windows": none.windows,
            "alarmed_windows": none.alarmed_windows,
            "alarmed_by": dict(sorted(none.alarmed_by.items())),
            "pairs": none.pairs,
            "alerted_pairs": none.alerted_pairs,
            "alerted_by": dict(sorted(none.alerted_by.items())),
            "window_upper": round(upper_bound(none.alarmed_windows, none.windows), 6),
            # An alert is raised only in a window whose own alarm holds, so its rate
            # is never above the single-window rate, however dependent consecutive
            # windows are: the tighter of the two bounds holds.
            "alert_upper": round(
                min(
                    upper_bound(none.alerted_pairs, none.pairs),
                    upper_bound(none.alarmed_windows, none.windows),
                ),
                6,
            ),
            "psi_max": {k: round(max(v), 6) for k, v in sorted(none.psi.items())},
            "psi_mean": {
                k: round(statistics.fmean(v), 6) for k, v in sorted(none.psi.items())
            },
            "psi_p95": {
                k: round(statistics.quantiles(v, n=20)[-1], 6)
                for k, v in sorted(none.psi.items())
            },
        },
        "detection": [],
    }
    for (kind, size), tally in tallies.items():
        if kind == "NONE":
            continue
        out["detection"].append(
            {
                "kind": kind,
                "size": size,
                "effect": round(statistics.fmean(tally.effects), 6),
                "windows": tally.windows,
                "alarmed_windows": tally.alarmed_windows,
                "pairs": tally.pairs,
                "alerted_pairs": tally.alerted_pairs,
                "alerted_by": dict(sorted(tally.alerted_by.items())),
                "explained": tally.explained,
                "psi_mean": {
                    k: round(statistics.fmean(v), 6)
                    for k, v in sorted(tally.psi.items())
                },
            }
        )
    return out


def digest(found: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(found, sort_keys=True).encode()).hexdigest()


# ---------- tables ----------


def version_table(result: dict[str, Any]) -> str:
    rule_set = result["rule_set"]
    reference = result["reference"]
    rows = [
        ["Customers in the reference", reference["sample_size"]],
        ["Recency, largest days scoring 5, 4, 3, 2", list(rule_set.recency)],
        ["Frequency, fewest purchases scoring 5, 4, 3, 2", list(rule_set.frequency)],
        [
            "Monetary, lowest spend scoring 5, 4, 3, 2",
            [f"{value:.2f}" for value in rule_set.monetary],
        ],
    ]
    rows += [
        [f"Reference share of `{item['label_code']}`", _pct(item["share"])]
        for item in reference["labels"]
    ]
    for feature in reference["features"]:
        edges = feature["bin_edges"]
        rows.append(
            [
                f"{feature['feature']} bins",
                f"{len(edges) - 1}, edges {edges[0]:g} to {edges[-1]:g}",
            ]
        )
    return table(["Measure", "Value"], rows)


def false_alarm_table(found: dict[str, Any]) -> str:
    fa = found["false_alarms"]
    limit = FALSE_ALARM_LIMIT

    def verdict(rate: float, upper: float) -> str:
        if upper <= limit:
            return f"within {_pct(limit)}, with 95% confidence"
        if rate <= limit:
            return f"within {_pct(limit)} as measured; the bound is above it"
        return f"above {_pct(limit)}"

    window_rate = fa["alarmed_windows"] / fa["windows"]
    alert_rate = fa["alerted_pairs"] / fa["pairs"]
    rows = [
        [
            "Single window: any variable at its alarm threshold",
            _rate(fa["alarmed_windows"], fa["windows"]),
            _pct(fa["window_upper"]),
            verdict(window_rate, fa["window_upper"]),
        ],
        [
            "Alert: the second of two consecutive windows still alarming",
            _rate(fa["alerted_pairs"], fa["pairs"]),
            _pct(fa["alert_upper"]),
            verdict(alert_rate, fa["alert_upper"]),
        ],
    ]
    note = (
        "An alert needs its own window at alarm, so the alert's bound is the tighter"
        " of its own and the single window's, whatever the overlap of consecutive"
        " windows."
    )
    head = ["False alarm", "Observed", "95% upper bound", "Against 1%"]
    return table(head, rows) + "\n\n" + note


def noise_table(found: dict[str, Any]) -> str:
    fa = found["false_alarms"]
    rows = [
        [
            name,
            f"{fa['psi_p95'][name]:.4f}",
            f"{fa['psi_max'][name]:.4f}",
            fa["alarmed_by"].get(name, 0),
        ]
        for name in (drift.SEGMENT_SHARES, "R", "F", "M")
    ]
    return table(
        ["Variable", "PSI, 95th percentile", "PSI, largest", "Windows at alarm"], rows
    )


def _effect(kind: str, value: float) -> str:
    if kind in ("SPEND", "FREQUENCY"):
        return f"{value * 100:+.1f}%"
    return f"{value * 100:+.1f} points"


def detection_table(found: dict[str, Any]) -> str:
    rows = []
    for item in found["detection"]:
        kind = item["kind"]
        share = f"{item['size'] * 100:g}% of sales"
        planted = {
            "SPEND": f"amounts x {1 + item['size']:g}",
            "FREQUENCY": f"{item['size'] * 100:g}% of older sales removed",
            "CATEGORY": f"{share} to category {PLANTED_CATEGORY}",
            "CHANNEL": f"{share} to channel {PLANTED_CHANNEL}",
        }[kind]
        by = ", ".join(f"{k} {v}" for k, v in item["alerted_by"].items()) or "none"
        explained = (
            _rate(item["explained"], item["alerted_pairs"])
            if kind in EXPECTED and item["alerted_pairs"]
            else "—"
        )
        rows.append(
            [
                kind,
                planted,
                _effect(kind, item["effect"]),
                _rate(item["alarmed_windows"], item["windows"]),
                _rate(item["alerted_pairs"], item["pairs"]),
                by,
                explained,
            ]
        )
    return table(
        [
            "Shift",
            "Planted",
            "Measured on the sales",
            "Windows at alarm",
            "Detected (alert)",
            "Alerts by variable",
            "Alert explains it",
        ],
        rows,
    )


def psi_table(found: dict[str, Any]) -> str:
    rows = [
        [
            item["kind"],
            f"{item['size']:g}",
            *(
                f"{item['psi_mean'][name]:.4f}"
                for name in (drift.SEGMENT_SHARES, "R", "F", "M")
            ),
        ]
        for item in found["detection"]
    ]
    names = (drift.SEGMENT_SHARES, "R", "F", "M")
    fa = found["false_alarms"]
    none = ["none", "0", *(f"{fa['psi_mean'][name]:.4f}" for name in names)]
    return table(["Shift", "Size", "Segment shares", "R", "F", "M"], [none, *rows])


def example_text(result: dict[str, Any], kind: str) -> str:
    """The first alert a shift raised, as a reader gets it, beside what was planted."""
    tallies: dict[tuple[str, float], Tally] = result["tallies"]
    for size in sorted(SHIFTS[kind], reverse=True):
        example = tallies[(kind, size)].example
        if example is None:
            continue
        planted = {
            "SPEND": f"every amount multiplied by {1 + size:g}",
            "FREQUENCY": f"{size * 100:g}% of each customer's older sales removed",
        }.get(kind, f"size {size:g}")
        lines = [
            f"**{kind} at {size:g}, window pair ending at draw {example['window']}.**"
            f" Planted: {planted}.",
            "",
        ]
        for alert in example["alerts"]:
            stats = "; ".join(
                f"{name} {value}"
                + (f" (p = {p:.3g})" if p is not None else "")
                + f", {level}"
                for name, value, p, level in alert["statistics"]
            )
            lines += [
                f"- `{alert['variable']}`, `{alert['review_code']}`: {stats}.",
                f"  > {alert['text']}",
            ]
        return "\n".join(lines)
    return f"**{kind}.** No alert was raised at any size."


def report(result: dict[str, Any] | None = None) -> str:
    result = result or run()
    found = figures(result)
    sections = [
        "## The version",
        version_table(result),
        "## False alarms: windows with no shift",
        f"{found['false_alarms']['windows']} windows, drawn at their own seeds from the"
        " generator that made the reference; consecutive draws form"
        f" {found['false_alarms']['pairs']} pairs.",
        false_alarm_table(found),
        noise_table(found),
        "## Detection: windows with a planted shift",
        f"{found['detection'][0]['pairs']} pairs per shift and size, planted on the"
        " first draws of the same no-shift windows.",
        detection_table(found),
        "### Mean PSI per variable (alarm at 0.25)",
        psi_table(found),
        "## What an alert says",
        example_text(result, "SPEND"),
        example_text(result, "FREQUENCY"),
        "## Repeatability",
        f"Study seed {STUDY_SEED}, reference seed {DEFAULT_SEED}, {result['customers']}"
        f" customers a window. SHA-256 of the figures: `{digest(found)}`",
    ]
    return "\n\n".join(sections)


def main(argv: Sequence[str] | None = None) -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DRIFT_STUDY_DATABASE_URL"),
        help="owner URL of a throwaway database loaded from sql/00, 01 and 02",
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DRIFT_STUDY_DATABASE_URL is required")
    print(report(run(database_url=args.database_url)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
