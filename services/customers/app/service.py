"""The customers rule: one registration per customer, never deleted (RN-08).

Email uniqueness (whatever the letter case, #594), the channel foreign key and
the email format are enforced by the schema; this module maps their refusals,
by constraint name, to the contract's errors. The address is lower-cased in SQL
as it is written, with the same lower() the unique index uses. The only rule
the schema cannot hold is that a registration date is not in the future, so the
INSERT itself refuses it.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

import psycopg
from flask import Flask, g, request
from lxml import etree
from mosaiq_kit import ApiError, Kit, Page, Representation, configure_logging
from psycopg import errors, sql

EMAIL_KEY = "ux_customer_email_lower"
CHANNEL_FKEY = "customer_registration_channel_id_fkey"
EMAIL_CHECK = "customer_email_check"
ACTIVE_CARD_KEY = "ux_loyalty_card_one_active_customer"
CUSTOMER_FKEY = "loyalty_card_customer_id_fkey"
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "contracts"

COLUMNS = sql.SQL(", ").join(
    sql.Identifier(name)
    for name in (
        "customer_id",
        "name",
        "email",
        "phone",
        "registration_channel_id",
        "registered_on",
    )
)

# The fields a correction may name; registered_on and customer_id are refused
# by the contract's schema before a handler runs.
CORRECTABLE = ("name", "email", "phone", "registration_channel_id")


def like_pattern(text: str) -> str:
    """A contains-pattern in which %, _ and the escape character are literal."""

    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + escaped + "%"


def text_filter(name: str) -> str | None:
    """A text query parameter held to its contract bounds, 1 to 160 characters.

    PostgreSQL text cannot hold U+0000, so a filter containing it is refused
    before it reaches the driver.
    """

    raw = request.args.get(name)
    if raw is None:
        return None
    if not 1 <= len(raw) <= 160:
        raise ApiError(
            422,
            "invalid_query",
            f"{name} is outside its allowed length.",
            [{"field": name, "code": "out_of_range"}],
        )
    if "\x00" in raw:
        raise ApiError(
            422,
            "invalid_query",
            f"{name} contains a NUL character.",
            [{"field": name, "code": "invalid_format"}],
        )
    return raw


def customer_json(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "customer_id": str(row[0]),
        "name": row[1],
        "email": row[2],
        "phone": row[3],
        "registration_channel_id": row[4],
        "registered_on": row[5],
    }


def floor_customer_json(row: tuple[Any, ...], role: str) -> dict[str, Any]:
    customer = {"customer_id": str(row[0]), "name": row[1]}
    if role == "LOYALTY_OFFICER":
        customer.update({"email": row[2], "phone": row[3]})
    return customer


def card_json(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "card_id": str(row[0]),
        "customer_id": str(row[1]),
        "code": row[2],
        "status": row[3],
        "issued_at": row[4],
        "issued_by": str(row[5]),
        "revoked_at": row[6],
        "revoked_by": str(row[7]) if row[7] is not None else None,
        "revocation_reason": row[8],
    }


def generate_card_code() -> str:
    """Encode 128 CSPRNG bits as exactly 26 Crockford base32 characters."""

    value = int.from_bytes(secrets.token_bytes(16), "big")
    encoded = []
    for _ in range(26):
        value, digit = divmod(value, 32)
        encoded.append(CROCKFORD[digit])
    return "".join(reversed(encoded))


CARD_COLUMNS = sql.SQL(", ").join(
    sql.Identifier(name)
    for name in (
        "card_id",
        "customer_id",
        "code",
        "status",
        "issued_at",
        "issued_by",
        "revoked_at",
        "revoked_by",
        "revocation_reason",
    )
)


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
    if isinstance(error, errors.UniqueViolation) and constraint == ACTIVE_CARD_KEY:
        return ApiError(
            409,
            "active_card_exists",
            "The customer already has an active loyalty card.",
            [{"field": "customer_id", "code": "already_active"}],
        )
    if isinstance(error, errors.ForeignKeyViolation) and constraint == CUSTOMER_FKEY:
        return ApiError(
            422,
            "unknown_customer",
            "The customer does not exist.",
            [{"field": "customer_id", "code": "not_found"}],
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

    @app.after_request
    def add_loyalty_state(response):
        """Add the list-wide state that is outside the kit's generic Page."""

        state = getattr(g, "loyalty_state", None)
        if state is None or response.status_code != 200:
            return response
        if response.mimetype == "application/json":
            payload = response.get_json()
            payload = {"loyalty_state": state, **payload}
            response.set_data(json.dumps(payload, separators=(",", ":")))
        elif response.mimetype == "application/xml":
            root = etree.fromstring(response.data)
            element = etree.Element(
                etree.QName(kit.contract.namespace, "loyalty_state")
            )
            element.text = state
            root.insert(0, element)
            response.set_data(
                etree.tostring(root, xml_declaration=True, encoding="UTF-8")
            )
        return response

    @kit.operation("searchCustomers")
    def search_customers() -> Page:
        q = text_filter("q")
        email = text_filter("email")
        pattern = like_pattern(q) if q is not None else None
        offset = (kit.page - 1) * kit.size
        # Fixed statements; the filters arrive only as bind parameters. LIKE
        # runs with an explicit ESCAPE so % and _ in q match themselves.
        filters = sql.SQL(
            "WHERE (%(pattern)s::text IS NULL"
            " OR name ILIKE %(pattern)s ESCAPE '\\'"
            " OR email ILIKE %(pattern)s ESCAPE '\\'"
            " OR phone ILIKE %(pattern)s ESCAPE '\\')"
            " AND (%(email)s::text IS NULL OR lower(email) = lower(%(email)s))"
        )
        params = {"pattern": pattern, "email": email}
        with kit.db.connection() as connection:
            total = connection.execute(
                sql.SQL("SELECT count(*) FROM public.customer {filters}").format(
                    filters=filters
                ),
                params,
            ).fetchone()[0]
            rows = connection.execute(
                sql.SQL(
                    "SELECT {columns} FROM public.customer {filters}"
                    " ORDER BY name, customer_id LIMIT %(limit)s OFFSET %(offset)s"
                ).format(columns=COLUMNS, filters=filters),
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
                sql.SQL("SELECT {} FROM public.customer WHERE customer_id = %s").format(
                    COLUMNS
                ),
                (customer_id,),
            ).fetchone()
        if row is None:
            raise not_found()
        return Representation("Customer", customer_json(row))

    @kit.operation("searchFloorCustomers")
    def search_floor_customers() -> Page:
        raw_q = request.args.get("q")
        q = raw_q.strip() if raw_q is not None else ""
        if raw_q is None:
            raise ApiError(
                422,
                "invalid_query",
                "q is required.",
                [{"field": "q", "code": "required"}],
            )
        if len(q) < 3:
            raise ApiError(
                422,
                "invalid_query",
                "q is shorter than its allowed length.",
                [{"field": "q", "code": "too_short"}],
            )
        if kit.size > 20:
            raise ApiError(
                422,
                "invalid_pagination",
                "The pagination values are outside their allowed ranges.",
                [{"field": "size", "code": "out_of_range"}],
            )
        role = str(g.principal["role"])
        pattern = like_pattern(q)
        if role == "SALES_ASSOCIATE":
            filters = sql.SQL("WHERE name ILIKE %(pattern)s ESCAPE '\\'")
        else:
            filters = sql.SQL(
                "WHERE (name ILIKE %(pattern)s ESCAPE '\\'"
                " OR email ILIKE %(pattern)s ESCAPE '\\'"
                " OR phone ILIKE %(pattern)s ESCAPE '\\')"
            )
        params = {"pattern": pattern}
        offset = (kit.page - 1) * kit.size
        with kit.db.connection() as connection:
            total = connection.execute(
                sql.SQL("SELECT count(*) FROM public.customer {filters}").format(
                    filters=filters
                ),
                params,
            ).fetchone()[0]
            rows = connection.execute(
                sql.SQL(
                    "SELECT customer_id, name, email, phone FROM public.customer"
                    " {filters} ORDER BY name, customer_id"
                    " LIMIT %(limit)s OFFSET %(offset)s"
                ).format(filters=filters),
                params | {"limit": kit.size, "offset": offset},
            ).fetchall()
        return Page(
            "FloorCustomers",
            "FloorCustomer",
            "customers",
            [floor_customer_json(row, role) for row in rows],
            page=kit.page,
            size=kit.size,
            total=total,
        )

    @kit.operation("getFloorCustomer")
    def get_floor_customer(customer_id: Any) -> Representation:
        with kit.db.connection() as connection:
            row = connection.execute(
                "SELECT customer_id, name, email, phone FROM public.customer"
                " WHERE customer_id = %s",
                (customer_id,),
            ).fetchone()
        if row is None:
            raise not_found()
        return Representation(
            "FloorCustomer", floor_customer_json(row, str(g.principal["role"]))
        )

    @kit.operation("issueLoyaltyCard")
    def issue_loyalty_card() -> Representation:
        try:
            with kit.db.transaction() as connection:
                row = connection.execute(
                    sql.SQL(
                        "INSERT INTO customers.loyalty_card"
                        " (customer_id, code, issued_by) VALUES (%s, %s, %s)"
                        " RETURNING {}"
                    ).format(CARD_COLUMNS),
                    (
                        kit.body["customer_id"],
                        generate_card_code(),
                        str(g.principal["sub"]),
                    ),
                ).fetchone()
        except psycopg.errors.IntegrityError as error:
            mapped = refusal(error)
            if mapped is None:
                raise
            raise mapped from None
        return Representation("LoyaltyCard", card_json(row), status=201)

    @kit.operation("listLoyaltyCards")
    def list_loyalty_cards() -> Page:
        customer_id = request.args["customer_id"]
        offset = (kit.page - 1) * kit.size
        with kit.db.connection() as connection:
            summary = connection.execute(
                "SELECT count(*), bool_or(status = 'ACTIVE')"
                " FROM customers.loyalty_card WHERE customer_id = %s",
                (customer_id,),
            ).fetchone()
            rows = connection.execute(
                sql.SQL(
                    "SELECT {} FROM customers.loyalty_card"
                    " WHERE customer_id = %s"
                    " ORDER BY issued_at DESC, card_id DESC LIMIT %s OFFSET %s"
                ).format(CARD_COLUMNS),
                (customer_id, kit.size, offset),
            ).fetchall()
        total, has_active = summary
        g.loyalty_state = (
            "NONE" if total == 0 else "ACTIVE" if has_active else "REVOKED"
        )
        return Page(
            "LoyaltyCards",
            "LoyaltyCard",
            "loyalty_cards",
            [card_json(row) for row in rows],
            page=kit.page,
            size=kit.size,
            total=total,
        )

    @kit.operation("lookupLoyaltyCard")
    def lookup_loyalty_card() -> Representation:
        with kit.db.connection() as connection:
            row = connection.execute(
                "SELECT card_id, customer_id, status"
                " FROM customers.loyalty_card WHERE code = %s",
                (kit.body["code"],),
            ).fetchone()
        if row is None:
            raise card_not_found("code")
        if row[2] == "REVOKED":
            raise ApiError(
                409,
                "card_revoked",
                "The loyalty card was revoked.",
                [{"field": "code", "code": "revoked"}],
            )
        return Representation(
            "LoyaltyCardLookup",
            {"card_id": str(row[0]), "customer_id": str(row[1])},
        )

    @kit.operation("revokeLoyaltyCard")
    def revoke_loyalty_card(card_id: Any) -> Representation:
        with kit.db.transaction() as connection:
            row = connection.execute(
                sql.SQL(
                    "UPDATE customers.loyalty_card SET status = 'REVOKED',"
                    " revoked_at = CURRENT_TIMESTAMP, revoked_by = %s,"
                    " revocation_reason = %s"
                    " WHERE card_id = %s AND status = 'ACTIVE' RETURNING {}"
                ).format(CARD_COLUMNS),
                (str(g.principal["sub"]), kit.body["reason"], card_id),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    sql.SQL(
                        "SELECT {} FROM customers.loyalty_card WHERE card_id = %s"
                    ).format(CARD_COLUMNS),
                    (card_id,),
                ).fetchone()
        if row is None:
            raise card_not_found("card_id")
        return Representation("LoyaltyCard", card_json(row))

    @kit.operation("registerCustomer")
    def register_customer() -> Representation:
        body = kit.body
        refuse_a_blank_name(body)
        try:
            with kit.db.transaction() as connection:
                row = connection.execute(
                    sql.SQL(
                        "INSERT INTO public.customer"
                        " (name, email, phone, registration_channel_id, registered_on) "
                        "SELECT %(name)s, lower(%(email)s), %(phone)s, %(channel)s,"
                        " coalesce(%(registered_on)s::date, CURRENT_DATE) "
                        "WHERE coalesce(%(registered_on)s::date, CURRENT_DATE)"
                        " <= CURRENT_DATE "
                        "RETURNING {}"
                    ).format(COLUMNS),
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
        refuse_a_blank_name(body)
        params: dict[str, Any] = {"customer_id": customer_id}
        for field in CORRECTABLE:
            params["set_" + field] = field in body
            params[field] = body.get(field)
        try:
            with kit.db.transaction() as connection:
                row = connection.execute(
                    sql.SQL(
                        "UPDATE public.customer SET"
                        " name = CASE WHEN %(set_name)s THEN %(name)s ELSE name END,"
                        " email = CASE WHEN %(set_email)s THEN lower(%(email)s)"
                        " ELSE email END,"
                        " phone = CASE WHEN %(set_phone)s THEN %(phone)s"
                        " ELSE phone END,"
                        " registration_channel_id = CASE"
                        " WHEN %(set_registration_channel_id)s"
                        " THEN %(registration_channel_id)s::smallint"
                        " ELSE registration_channel_id END "
                        "WHERE customer_id = %(customer_id)s "
                        "RETURNING {}"
                    ).format(COLUMNS),
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


def refuse_a_blank_name(body: dict[str, Any]) -> None:
    """Refuse a name that is empty or only whitespace (#587).

    The schema bounds only its length, so "" and "   " would be stored; the
    contract documents this refusal under invalid_body.
    """
    if "name" in body and not body["name"].strip():
        raise ApiError(
            422,
            "invalid_body",
            "The parsed body violates the request schema.",
            [{"field": "name", "code": "too_short"}],
        )


def not_found() -> ApiError:
    return ApiError(
        404,
        "customer_not_found",
        "The customer does not exist.",
        [{"field": "customer_id", "code": "not_found"}],
    )


def card_not_found(field: str) -> ApiError:
    return ApiError(
        404,
        "card_not_found",
        "No loyalty card matches.",
        [{"field": field, "code": "not_found"}],
    )
