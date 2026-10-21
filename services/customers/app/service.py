"""The customers rule: one registration per customer, never deleted (RN-08).

Email uniqueness, the channel foreign key and the email format are enforced by
the schema; this module maps their refusals, by constraint name, to the
contract's errors. The only rule the schema cannot hold is that a registration
date is not in the future, so the INSERT itself refuses it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
from flask import Flask, request
from mosaiq_kit import ApiError, Kit, Page, Representation, configure_logging
from psycopg import errors

EMAIL_KEY = "customer_email_key"
CHANNEL_FKEY = "customer_registration_channel_id_fkey"
EMAIL_CHECK = "customer_email_check"

CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "contracts"

COLUMNS = "customer_id, name, email, phone, registration_channel_id, registered_on"

# The fields a correction may name; registered_on and customer_id are refused
# by the contract's schema before a handler runs.
CORRECTABLE = ("name", "email", "phone", "registration_channel_id")


def like_pattern(text: str) -> str:
    """A contains-pattern in which %, _ and the escape character are literal."""

    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + escaped + "%"


def text_filter(name: str) -> str | None:
    """A text query parameter; an empty one filters nothing.

    PostgreSQL text cannot hold U+0000, so a filter containing it is refused
    before it reaches the driver.
    """

    value = request.args.get(name) or None
    if value is not None and "\x00" in value:
        raise ApiError(
            422,
            "invalid_pagination",
            "A query value is outside its allowed range.",
            [{"field": name, "code": "invalid_format"}],
        )
    return value


def customer_json(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "customer_id": str(row[0]),
        "name": row[1],
        "email": row[2],
        "phone": row[3],
        "registration_channel_id": row[4],
        "registered_on": row[5],
    }


def email_taken() -> ApiError:
    return ApiError(
        409,
        "email_taken",
        "The email already belongs to another customer.",
        [{"field": "email", "code": "not_unique"}],
    )


def refusal(error: psycopg.errors.IntegrityError) -> ApiError | None:
    """The contract's error for a constraint of customer, or None."""

    constraint = error.diag.constraint_name
    if isinstance(error, errors.UniqueViolation) and constraint == EMAIL_KEY:
        return email_taken()
    if isinstance(error, errors.ForeignKeyViolation) and constraint == CHANNEL_FKEY:
        return ApiError(
            422,
            "unknown_channel",
            "The registration channel does not exist.",
            [{"field": "registration_channel_id", "code": "unknown_reference"}],
        )
    if isinstance(error, errors.CheckViolation) and constraint == EMAIL_CHECK:
        return ApiError(
            422,
            "invalid_body",
            "The parsed body violates the request schema.",
            [{"field": "email", "code": "invalid_format"}],
        )
    return None


def create_app() -> Flask:
    app = Flask(__name__)
    configure_logging()
    holder: dict[str, Kit] = {}

    def schema_ready() -> bool:
        with holder["kit"].db.connection() as connection:
            connection.execute(
                "SELECT customer_id FROM api_customers.customer_ref_v1 LIMIT %s",
                (1,),
            ).fetchall()
        return True

    kit = Kit(
        app,
        service="customers",
        suffix="customers",
        contracts_dir=CONTRACTS_DIR,
        ready_checks=[schema_ready],
    )
    holder["kit"] = kit
    app.extensions["mosaiq_kit"] = kit

    @kit.operation("searchCustomers")
    def search_customers() -> Page:
        q = text_filter("q")
        email = text_filter("email")
        pattern = like_pattern(q) if q is not None else None
        offset = (kit.page - 1) * kit.size
        # Fixed statements; the filters arrive only as bind parameters. LIKE
        # runs with an explicit ESCAPE so % and _ in q match themselves.
        filters = (
            "WHERE (%(pattern)s::text IS NULL"
            " OR name ILIKE %(pattern)s ESCAPE '\\'"
            " OR email ILIKE %(pattern)s ESCAPE '\\'"
            " OR phone ILIKE %(pattern)s ESCAPE '\\')"
            " AND (%(email)s::text IS NULL OR email = %(email)s)"
        )
        params = {"pattern": pattern, "email": email}
        with kit.db.connection() as connection:
            total = connection.execute(
                "SELECT count(*) FROM public.customer " + filters, params
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT "
                + COLUMNS
                + " FROM public.customer "
                + filters
                + " ORDER BY name, customer_id LIMIT %(limit)s OFFSET %(offset)s",
                params | {"limit": kit.size, "offset": offset},
            ).fetchall()
        return Page(
            "Customers",
            "Customer",
            "customers",
            [customer_json(row) for row in rows],
            page=kit.page,
            size=kit.size,
            total=total,
        )

    @kit.operation("getCustomer")
    def get_customer(customer_id: Any) -> Representation:
        with kit.db.connection() as connection:
            row = connection.execute(
                "SELECT " + COLUMNS + " FROM public.customer WHERE customer_id = %s",
                (customer_id,),
            ).fetchone()
        if row is None:
            raise not_found()
        return Representation("Customer", customer_json(row))

    @kit.operation("registerCustomer")
    def register_customer() -> Representation:
        body = kit.body
        try:
            with kit.db.transaction() as connection:
                row = connection.execute(
                    "INSERT INTO public.customer"
                    " (name, email, phone, registration_channel_id, registered_on) "
                    "SELECT %(name)s, %(email)s, %(phone)s, %(channel)s,"
                    " coalesce(%(registered_on)s::date, CURRENT_DATE) "
                    "WHERE coalesce(%(registered_on)s::date, CURRENT_DATE)"
                    " <= CURRENT_DATE "
                    "RETURNING " + COLUMNS,
                    {
                        "name": body["name"],
                        "email": body.get("email"),
                        "phone": body.get("phone"),
                        "channel": body["registration_channel_id"],
                        "registered_on": body.get("registered_on"),
                    },
                ).fetchone()
        except psycopg.errors.IntegrityError as error:
            mapped = refusal(error)
            if mapped is None:
                raise
            raise mapped from None
        if row is None:
            raise ApiError(
                422,
                "future_registration",
                "The registration date cannot be in the future.",
                [{"field": "registered_on", "code": "future_date"}],
            )
        customer = customer_json(row)
        return Representation(
            "Customer",
            customer,
            status=201,
            headers={"Location": "/v1/customers/" + customer["customer_id"]},
        )

    @kit.operation("correctCustomer")
    def correct_customer(customer_id: Any) -> Representation:
        body = kit.body
        params: dict[str, Any] = {"customer_id": customer_id}
        for field in CORRECTABLE:
            params["set_" + field] = field in body
            params[field] = body.get(field)
        try:
            with kit.db.transaction() as connection:
                row = connection.execute(
                    "UPDATE public.customer SET"
                    " name = CASE WHEN %(set_name)s THEN %(name)s ELSE name END,"
                    " email = CASE WHEN %(set_email)s THEN %(email)s ELSE email END,"
                    " phone = CASE WHEN %(set_phone)s THEN %(phone)s ELSE phone END,"
                    " registration_channel_id = CASE"
                    " WHEN %(set_registration_channel_id)s"
                    " THEN %(registration_channel_id)s::smallint"
                    " ELSE registration_channel_id END "
                    "WHERE customer_id = %(customer_id)s "
                    "RETURNING " + COLUMNS,
                    params,
                ).fetchone()
        except psycopg.errors.IntegrityError as error:
            mapped = refusal(error)
            if mapped is None:
                raise
            raise mapped from None
        if row is None:
            raise not_found()
        return Representation("Customer", customer_json(row))

    return app


def not_found() -> ApiError:
    return ApiError(
        404,
        "customer_not_found",
        "The customer does not exist.",
        [{"field": "customer_id", "code": "not_found"}],
    )
