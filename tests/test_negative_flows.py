"""F5-02 (#75): refusal evidence through the registered Flask routes.

Only the PostgreSQL connector is replaced. Routes, authorization, validation,
password verification, queries, templates and upload storage run normally.
"""

import re
from io import BytesIO
from unittest.mock import MagicMock, Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from markupsafe import escape
from psycopg import OperationalError
from psycopg.errors import ForeignKeyViolation, UniqueViolation

from web.app import create_app
from web.config import Config
from web.middleware.authz import requirement_of
from web.services.auth import hash_password

USER_ID = "11111111-1111-1111-1111-000000000007"
CATALOGS = ("stores", "categories", "channels", "products", "roles")
READ_ROUTES = [
    *(f"/admin/{catalog}" for catalog in CATALOGS),
    "/admin/catalogs",
    "/admin/users",
    "/admin/products/image/missing.png",
    "/audit/",
    "/audit/1",
    "/catalog/",
    "/catalog/products",
    "/catalog/products/1",
    "/catalog/customers",
    f"/catalog/customers/{USER_ID}",
    "/catalog/stock",
    "/catalog/segments",
    "/catalog/segments/1",
]
ADMIN_ROUTES = [
    (method, f"/admin/{catalog}/{suffix}")
    for catalog in CATALOGS
    for method, suffix in (
        ("GET", "new"),
        ("POST", "new"),
        ("GET", "1/edit"),
        ("POST", "1/edit"),
        ("POST", "1/delete"),
    )
] + [
    ("GET", "/admin/users/new"),
    ("POST", "/admin/users/new"),
    ("POST", f"/admin/users/{USER_ID}/activate"),
    ("POST", f"/admin/users/{USER_ID}/deactivate"),
    ("GET", "/segment-run/"),
    ("POST", "/segment-run/"),
]
PROTECTED_ROUTES = [("GET", path) for path in READ_ROUTES] + ADMIN_ROUTES
PRODUCT = dict(
    product_id="1", sku="TEST", name="Product", category_id="1", list_price="10"
)
USER = dict(
    name="Test User",
    email="user@example.test",
    password="TestPassword!75",
    role_code="CUSTOMER",
)


@pytest.fixture
def connection():
    connection = MagicMock()
    connection.closed = False
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    return connection


@pytest.fixture
def app(connection, tmp_path):
    connector = Mock(side_effect=[Mock(), connection])
    app = create_app(
        Config(
            secret_key="negative-flow-test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            database_url="unused-by-test",
            upload_dir=str(tmp_path / "uploads"),
            max_upload_bytes=10,
        ),
        database_connector=connector,
    )
    app.config["PROPAGATE_EXCEPTIONS"] = False
    connector.reset_mock()
    return app


def sign_in(client, role="ADMIN"):
    with client.session_transaction() as session:
        session.update(user_id=USER_ID, role_code=role, name="Test User")


def assert_page(response, status, message):
    body = response.get_data(as_text=True)
    assert response.status_code == status
    assert response.mimetype == "text/html"
    assert "MOSAIQ" in body and message in body
    assert all(text not in body for text in ("Traceback", "Werkzeug", "psycopg"))


def assert_no_writes(connection):
    cursor = connection.cursor.return_value.__enter__.return_value
    assert all(
        call.args[0].lstrip().upper().startswith("SELECT")
        for call in cursor.execute.call_args_list
    )
    connection.commit.assert_not_called()


def test_refusal_matrix_covers_every_protected_route(app):
    adapter = app.url_map.bind("localhost")
    covered = {
        (adapter.match(path, method=method)[0], method)
        for method, path in PROTECTED_ROUTES + [("POST", "/logout")]
    }
    registered = {
        (rule.endpoint, method)
        for rule in app.url_map.iter_rules()
        if (requirement := requirement_of(app.view_functions[rule.endpoint]))
        and not requirement.anonymous_allowed
        for method in rule.methods - {"HEAD", "OPTIONS"}
    }
    assert covered == registered


@pytest.mark.parametrize("method,path", PROTECTED_ROUTES + [("POST", "/logout")])
def test_anonymous_access(app, method, path):
    response = app.test_client().open(path, method=method)
    if method == "GET":
        assert response.status_code == 302
        target = urlsplit(response.location)
        assert target.path == "/login" and not target.netloc
        assert parse_qs(target.query) == {"next": [path]}
    else:
        assert_page(response, 403, "Forbidden")
    app.extensions["database_connector"].assert_not_called()


@pytest.mark.parametrize("method,path", PROTECTED_ROUTES)
def test_customer_access(app, method, path, caplog):
    client = app.test_client()
    sign_in(client, "CUSTOMER")
    assert_page(client.open(path, method=method), 403, "Forbidden")
    assert "Access denied" in caplog.text and USER_ID in caplog.text
    app.extensions["database_connector"].assert_not_called()


@pytest.mark.parametrize(
    "role", ["ANALYST", "STORE_MANAGER", "MARKETING", "INVENTORY_PLANNER", "AUDITOR"]
)
@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_other_roles_cannot_administer(app, method, path, role):
    client = app.test_client()
    sign_in(client, role)
    assert_page(client.open(path, method=method), 403, "Forbidden")
    app.extensions["database_connector"].assert_not_called()


def test_forged_session_is_anonymous(app):
    client = app.test_client()
    client.set_cookie("session", "forged-administrator-cookie")
    response = client.get("/admin/users")
    assert response.status_code == 302
    assert urlsplit(response.location).path == "/login"
    app.extensions["database_connector"].assert_not_called()


@pytest.fixture(scope="module")
def password_hash():
    return hash_password(USER["password"])


@pytest.mark.parametrize(
    "case", ["unknown", "wrong-password", "inactive", "empty", "sql-injection"]
)
def test_rejected_login(app, connection, password_hash, case, caplog):
    cursor = connection.cursor.return_value.__enter__.return_value
    email, password = USER["email"], USER["password"]
    if case in {"wrong-password", "inactive"}:
        cursor.fetchone.return_value = (
            USER_ID,
            7,
            "CUSTOMER",
            "Customer",
            "Test User",
            email,
            password_hash,
            case != "inactive",
        )
    if case == "wrong-password":
        password = "IncorrectPassword!75"
    elif case == "empty":
        email, password = "", ""
    elif case == "sql-injection":
        email = "' OR 1=1 --"
    client = app.test_client()
    assert_page(
        client.post("/login", data=dict(email=email, password=password)),
        401,
        "Invalid email or password.",
    )
    with client.session_transaction() as session:
        assert "user_id" not in session and "role_code" not in session
    statement, parameters = cursor.execute.call_args.args
    assert "WHERE u.email = %s" in statement and parameters == (email,)
    if email:
        assert email not in statement
    assert USER["password"] not in caplog.text and password_hash not in caplog.text
    assert_no_writes(connection)


@pytest.mark.parametrize(
    "catalog,message",
    [
        ("stores", "Name is required."),
        ("categories", "Name is required."),
        ("channels", "Name is required."),
        ("products", "SKU is required."),
        ("roles", "Code is required."),
        ("users", "A valid email is required."),
    ],
)
def test_empty_creation_is_rejected(app, connection, catalog, message):
    client = app.test_client()
    sign_in(client)
    assert_page(client.post(f"/admin/{catalog}/new", data={}), 400, message)
    assert_no_writes(connection)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("product_id", "1; DROP TABLE product", "whole number"),
        ("sku", "x" * 41, "40 characters"),
        ("name", "x" * 151, "150 characters"),
        ("category_id", "invalid", "Category is required."),
        ("list_price", "-1", "cannot be negative"),
        ("list_price", "invalid", "must be a number"),
    ],
)
def test_invalid_product_is_rejected(app, connection, field, value, message):
    client = app.test_client()
    sign_in(client)
    assert_page(
        client.post("/admin/products/new", data=PRODUCT | {field: value}), 400, message
    )
    assert_no_writes(connection)


def test_rejected_form_escapes_script_and_does_not_echo_password(app, connection):
    client = app.test_client()
    sign_in(client)
    script = '<script>alert("xss")</script>'
    response = client.post(
        "/admin/users/new", data=USER | {"name": script, "email": "invalid"}
    )
    assert_page(response, 400, "A valid email is required.")
    assert script.encode() not in response.data
    assert str(escape(script)).encode() in response.data
    assert USER["password"].encode() not in response.data
    assert_no_writes(connection)


@pytest.mark.parametrize("catalog", [*CATALOGS, "users"])
def test_search_payload_remains_a_parameter(app, connection, catalog):
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (0,)
    client = app.test_client()
    sign_in(client)
    payload = "<script>alert(1)</script>' OR 1=1 --"
    response = client.get(f"/admin/{catalog}", query_string={"q": payload})
    assert_page(response, 200, str(escape(payload)))
    searches = [
        call for call in cursor.execute.call_args_list if "ILIKE" in call.args[0]
    ]
    assert len(searches) == 2
    for call in searches:
        statement, parameters = call.args
        assert payload not in statement and f"%{payload}%" in parameters
    assert payload.encode() not in response.data
    assert_no_writes(connection)


@pytest.mark.parametrize(
    "content,mime,message",
    [
        (b"script", "text/html", "not an accepted image type"),
        (b"", "image/png", "image file is empty"),
        (b"x" * 11, "image/png", "File is too large"),
    ],
)
def test_rejected_upload_leaves_no_product_or_file(
    app, connection, tmp_path, content, mime, message
):
    client = app.test_client()
    sign_in(client)
    response = client.post(
        "/admin/products/new",
        data=PRODUCT | {"image": (BytesIO(content), "image.png", mime)},
    )
    assert_page(response, 400, message)
    assert list(tmp_path.rglob("*")) == []
    assert_no_writes(connection)


@pytest.mark.parametrize(
    "error,message",
    [
        (UniqueViolation, "ID or SKU already exists"),
        (ForeignKeyViolation, "category does not exist"),
    ],
)
def test_database_refusal_rolls_back_and_explains_the_conflict(
    app, connection, error, message
):
    def execute(statement, parameters=None):
        if statement.lstrip().startswith("INSERT"):
            raise error("private database detail")

    connection.cursor.return_value.__enter__.return_value.execute.side_effect = execute
    client = app.test_client()
    sign_in(client)
    response = client.post("/admin/products/new", data=PRODUCT)
    assert_page(response, 409, message)
    assert b"private database detail" not in response.data
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


@pytest.mark.parametrize(
    "method,path,status", [("GET", "/missing", 404), ("GET", "/logout", 405)]
)
def test_routing_errors_are_controlled(app, method, path, status):
    assert_page(app.test_client().open(path, method=method), status, "Reference")


def test_database_outage_is_controlled_and_traceable(app, caplog):
    app.extensions["database_connector"].side_effect = OperationalError(
        "private database detail"
    )
    response = app.test_client().post("/login", data=USER)
    assert_page(response, 500, "Internal Server Error")
    reference = re.search(rb"<code>([0-9a-f]{8})</code>", response.data)
    assert reference and reference[1].decode() in caplog.text
    assert "POST /login" in caplog.text
    assert b"private database detail" not in response.data
