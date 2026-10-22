"""mongo/init.js and mongo/seed.js, run for real in mongosh.

They need a MongoDB with authorization on, root's URI in TEST_MONGO_ROOT_URI
and mongosh (on the PATH, or TEST_MONGOSH). The shape is shared, so these run
against the database the other tests use: init.js is idempotent, and the
seed's events are fixed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from pymongo import MongoClient
from pymongo.errors import OperationFailure, WriteError

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "mongo/init.js"
SEED = ROOT / "mongo/seed.js"
# The password init.js was given for the test MongoDB: the one in the service
# user's own URI, so running it again changes nothing.
PASSWORD = (
    urlsplit(os.environ["TEST_MONGO_URI"]).password
    if os.environ.get("TEST_MONGO_URI")
    else "svc_engagement"
)

pytestmark = pytest.mark.mongo


@pytest.fixture(scope="module")
def mongosh() -> str:
    command = os.environ.get("TEST_MONGOSH") or shutil.which("mongosh")
    if not command or not os.environ.get("TEST_MONGO_ROOT_URI"):
        if os.environ.get("MOSAIQ_REQUIRE_MONGO") == "1":
            pytest.fail(
                "mongosh and TEST_MONGO_ROOT_URI are missing, and"
                " MOSAIQ_REQUIRE_MONGO forbids skipping"
            )
        pytest.skip("mongosh and TEST_MONGO_ROOT_URI are missing")
    return command


def run(mongosh: str, script: Path, uri: str, password: str | None = PASSWORD):
    env = {k: v for k, v in os.environ.items() if k != "MONGO_ENGAGEMENT_PASSWORD"}
    if password is not None:
        env["MONGO_ENGAGEMENT_PASSWORD"] = password
    return subprocess.run(
        [mongosh, "--quiet", "--norc", uri, "--file", str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.fixture
def root_uri(mongosh) -> str:
    return os.environ["TEST_MONGO_ROOT_URI"]


def snapshot(root: MongoClient) -> dict:
    """Everything init.js is responsible for, as plain data."""

    database = root["engagement"]
    info = {c["name"]: c for c in database.list_collections()}
    user = root["admin"].command(
        "usersInfo", {"user": "svc_engagement", "db": "engagement"}
    )["users"]
    return {
        "collections": sorted(info),
        "validator": info["engagement_event"]["options"],
        "indexes": {
            name: sorted(
                (i["name"], tuple(i["key"].items()), bool(i.get("unique")))
                for i in database[name].list_indexes()
            )
            for name in info
        },
        "roles": [u["roles"] for u in user],
    }


@pytest.fixture
def admin(mongosh, root_uri):
    client: MongoClient = MongoClient(root_uri, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


def test_init_is_idempotent(mongosh, root_uri, admin):
    first = run(mongosh, INIT, root_uri)
    assert first.returncode == 0, first.stderr + first.stdout
    before = snapshot(admin)
    second = run(mongosh, INIT, root_uri)
    assert second.returncode == 0, second.stderr
    assert snapshot(admin) == before
    assert "created" not in second.stdout and "dropped" not in second.stdout
    assert before["collections"] == ["engagement_daily", "engagement_event"]
    assert before["roles"] == [[{"role": "readWrite", "db": "engagement"}]]
    names = sorted(i[0] for i in before["indexes"]["engagement_event"])
    assert names == [
        "_id_",
        "client_event_id_1",
        "customer_id_1_occurred_at_-1",
        "store_id_1_occurred_at_-1",
        "type_1_occurred_at_-1",
    ]
    daily = before["indexes"]["engagement_daily"]
    assert (
        "store_id_1_day_1_type_1",
        (("store_id", 1), ("day", 1), ("type", 1)),
        True,
    ) in daily


def test_init_refuses_to_run_without_a_password(mongosh, root_uri, admin):
    before = snapshot(admin)
    refused = run(mongosh, INIT, root_uri, password=None)
    assert refused.returncode != 0
    assert "MONGO_ENGAGEMENT_PASSWORD" in refused.stdout + refused.stderr
    assert snapshot(admin) == before
    assert run(mongosh, INIT, root_uri, password="").returncode != 0


def test_init_brings_a_drifted_shape_back_to_its_definition(mongosh, root_uri, admin):
    assert run(mongosh, INIT, root_uri).returncode == 0
    wanted = snapshot(admin)
    database = admin["engagement"]
    database.command("collMod", "engagement_event", validator={}, validationLevel="off")
    database["engagement_event"].drop_index("client_event_id_1")
    database["engagement_event"].create_index(
        "client_event_id", name="client_event_id_1"
    )  # no longer unique
    database["engagement_event"].create_index("product_id", name="stray")
    database["engagement_daily"].drop_index("store_id_1_day_1_type_1")
    admin["engagement"].command(
        "updateUser", "svc_engagement", roles=[{"role": "read", "db": "engagement"}]
    )
    assert snapshot(admin) != wanted
    fixed = run(mongosh, INIT, root_uri)
    assert fixed.returncode == 0, fixed.stderr
    assert snapshot(admin) == wanted


def test_the_service_user_can_sign_in_with_the_password_init_read(
    mongosh, root_uri, mongo_uri
):
    assert run(mongosh, INIT, root_uri).returncode == 0
    client: MongoClient = MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
    try:
        assert client["engagement"].list_collection_names()
        with pytest.raises(OperationFailure):
            client["admin"].list_collection_names()
        with pytest.raises(OperationFailure):
            client["other"].create_collection("nope")
    finally:
        client.close()


# ---- the validator -------------------------------------------------------------


def event(**overrides):
    base = {
        "_id": str(uuid.uuid4()),
        "client_event_id": str(uuid.uuid4()),
        "type": "QR_SCAN",
        "store_id": 1,
        "product_id": 1,
        "recorded_by": "validator-test",
        "occurred_at": datetime.now(UTC),
        "recorded_at": datetime.now(UTC),
        "voided": False,
        "history": [],
    }
    return {k: v for k, v in (base | overrides).items() if v is not ...}


@pytest.mark.parametrize(
    "overrides",
    [
        {"rank": 1},
        {"label_code": "LOYAL"},
        {"type": "RECO_CLICK"},
        {"type": "RECO_CLICK", "label_code": "LOYAL"},
        {"type": "RECO_CLICK", "rank": 1},
        {"type": "RECO_CLICK", "label_code": "L" * 41, "rank": 1},
        {"type": "RECO_CLICK", "label_code": "LOYAL", "rank": 0},
        {"type": "RECO_CLICK", "label_code": "LOYAL", "rank": 1.5},
        {"type": "SCAN"},
        {"store_id": "1"},
        {"store_id": 1.0},
        {"customer_id": "not-a-uuid"},
        {"client_event_id": "not-a-uuid"},
        {"_id": "not-a-uuid"},
        {"name": "Ada Lovelace"},
        {"email": "ada@example.com"},
        {"latitude": 25.6},
        {"occurred_at": "2026-10-15"},
        {"voided": "no"},
        {
            "history": [
                {"action": "DELETED", "reason": "x", "by": "u", "at": datetime.now(UTC)}
            ]
        },
        {
            "history": [
                {"action": "VOIDED", "reason": "", "by": "u", "at": datetime.now(UTC)}
            ]
        },
        {
            "history": [
                {
                    "action": "VOIDED",
                    "reason": "x" * 501,
                    "by": "u",
                    "at": datetime.now(UTC),
                }
            ]
        },
        {"history": [{"action": "VOIDED", "reason": "x", "by": "u"}]},
        {"voided": ...},
        {"history": ...},
        {"recorded_by": ...},
    ],
)
def test_the_validator_refuses_what_is_not_an_event(
    mongosh, root_uri, mongo, overrides
):
    assert run(mongosh, INIT, root_uri).returncode == 0
    events = mongo.get_default_database(default="engagement").engagement_event
    document = event(**overrides)
    with pytest.raises(WriteError) as refused:
        events.insert_one(document)
    assert refused.value.code == 121
    assert "Document failed validation" in str(refused.value)


def test_the_validator_accepts_both_shapes(mongosh, root_uri, mongo):
    assert run(mongosh, INIT, root_uri).returncode == 0
    events = mongo.get_default_database(default="engagement").engagement_event
    good = [
        event(),
        event(customer_id=str(uuid.uuid4())),
        event(type="RECO_CLICK", label_code="LOYAL", rank=2),
        event(
            label_code=...,
            history=[
                {"action": "VOIDED", "reason": "x", "by": "u", "at": datetime.now(UTC)}
            ],
            voided=True,
        ),
    ]
    try:
        for document in good:
            events.insert_one(document)
    finally:
        events.delete_many({"recorded_by": "validator-test"})


# ---- the seed --------------------------------------------------------------------


def test_the_seed_inserts_once_and_agrees_with_its_rollup(
    mongosh, root_uri, mongo_uri, mongo
):
    assert run(mongosh, INIT, root_uri).returncode == 0
    first = run(mongosh, SEED, mongo_uri)
    assert first.returncode == 0, first.stderr + first.stdout
    database = mongo.get_default_database(default="engagement")
    counts = {
        t: database.engagement_event.count_documents(
            {"type": t, "recorded_by": "5eed0000-0000-4000-8000-000000000001"}
        )
        for t in ("QR_SCAN", "RECO_CLICK")
    }
    assert counts["QR_SCAN"] >= 30 and counts["RECO_CLICK"] >= 30
    before = sorted(
        map(
            str,
            database.engagement_daily.find(
                {"day": {"$gte": "2026-10-01", "$lte": "2026-10-14"}}
            ),
        )
    )
    events_before = database.engagement_event.count_documents({})
    second = run(mongosh, SEED, mongo_uri)
    assert second.returncode == 0, second.stderr
    assert " 0 events inserted" in second.stdout
    assert database.engagement_event.count_documents({}) == events_before
    assert (
        sorted(
            map(
                str,
                database.engagement_daily.find(
                    {"day": {"$gte": "2026-10-01", "$lte": "2026-10-14"}}
                ),
            )
        )
        == before
    )
    # The compliance check of ADR-0038: the rollup is what the events add up to.
    rebuilt = list(
        database.engagement_event.aggregate(
            [
                {
                    "$match": {
                        "voided": False,
                        "client_event_id": {"$regex": "^5eed000[24]"},
                    }
                },
                {
                    "$group": {
                        "_id": {
                            "store_id": "$store_id",
                            "type": "$type",
                            "day": {
                                "$dateToString": {
                                    "format": "%Y-%m-%d",
                                    "date": "$occurred_at",
                                    "timezone": "UTC",
                                }
                            },
                        },
                        "count": {"$sum": 1},
                    }
                },
            ]
        )
    )
    wrong = [
        r
        for r in rebuilt
        if (
            database.engagement_daily.find_one(
                {
                    "store_id": r["_id"]["store_id"],
                    "day": r["_id"]["day"],
                    "type": r["_id"]["type"],
                }
            )
            or {"count": -1}
        )["count"]
        != r["count"]
    ]
    assert wrong == []
    voided = database.engagement_event.count_documents(
        {"client_event_id": {"$regex": "^5eed000[24]"}, "voided": True}
    )
    assert voided == 3
