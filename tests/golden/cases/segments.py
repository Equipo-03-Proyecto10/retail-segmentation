"""RN-39, RN-59 and RN-60 through the web's pure migration functions."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import psycopg

from tests.golden.cases import Case, Context
from web.services.segment_migration import (
    build_migration_matrix,
    classify_migration,
    describe_migration,
    explain_migration,
)

ORDINALS = {"CHAMPION": 1, "LOYAL": 2, "AT_RISK": 3}


def _classification(connection: psycopg.Connection) -> dict[str, Any]:
    del connection
    rows = classify_migration(
        {"a": "LOYAL", "b": "CHAMPION", "c": None},
        {"a": "CHAMPION", "b": "LOYAL", "c": None, "d": "AT_RISK"},
        ORDINALS,
    )
    return {
        "migrations": [
            {
                "customer_id": row.customer_id,
                "before": row.label_before,
                "after": row.label_after,
                "category": row.category.value,
                "direction": row.direction.value if row.direction else None,
            }
            for row in rows
        ]
    }


def _matrix(connection: psycopg.Connection) -> dict[str, Any]:
    del connection
    migrations = classify_migration(
        {"a": "LOYAL", "b": "CHAMPION", "c": None},
        {"a": "CHAMPION", "b": "LOYAL", "c": None, "d": "AT_RISK"},
        ORDINALS,
    )
    matrix = build_migration_matrix(migrations, ORDINALS)
    return {
        "row_totals": matrix.row_totals,
        "column_totals": matrix.column_totals,
        "nonzero_cells": {
            f"{row}->{column}": count
            for row, columns in matrix.cells.items()
            for column, count in columns.items()
            if count
        },
    }


def _assignment(context: Context, *, later: bool) -> SimpleNamespace:
    return SimpleNamespace(
        label_code="CHAMPION" if later else "LOYAL",
        recency_last_purchase_at=context.before_anchor(9 if later else 41),
        frequency_count=8,
        monetary_total=Decimal("1560.00" if later else "1200.00"),
        r_score=5 if later else 3,
        f_score=4,
        m_score=5 if later else 3,
    )


def _explanation(connection: psycopg.Connection, context: Context) -> dict[str, Any]:
    del connection
    explanation = explain_migration(
        _assignment(context, later=False), _assignment(context, later=True)
    )
    narrative = describe_migration(
        explanation,
        run_at_before=context.anchor,
        run_at_after=context.anchor,
    )
    return {
        "judgements": [sentence.judgement.value for sentence in narrative.sentences],
        "rank_only": [sentence.rank_only for sentence in narrative.sentences],
        "moved_by_rank_only": narrative.moved_by_rank_only,
        "recency_days": [
            narrative.recency_days_before,
            narrative.recency_days_after,
        ],
    }


def build(connection: psycopg.Connection, context: Context) -> list[Case]:
    del connection
    return [
        Case(
            "rn39-label-comparison",
            {
                "call": "classify_migration",
                "labels_before": {"a": "LOYAL", "b": "CHAMPION", "c": None},
                "labels_after": {
                    "a": "CHAMPION",
                    "b": "LOYAL",
                    "c": None,
                    "d": "AT_RISK",
                },
                "ordinals": ORDINALS,
            },
            _classification,
        ),
        Case(
            "rn59-traceable-matrix",
            {"call": "build_migration_matrix", "source": "rn39-label-comparison"},
            _matrix,
        ),
        Case(
            "rn60-raw-value-judgement",
            {
                "call": "describe_migration",
                "recency": [41, 9],
                "frequency": [8, 8],
                "monetary": ["1200.00", "1560.00"],
            },
            lambda c: _explanation(c, context),
        ),
    ]


def as_of(context: Context) -> dict[str, Any]:
    return {"anchor": context.anchor}
