//! The queries against the seeded database, as `svc_audit_trail`.

mod support;

use audit_trail::{audit_entry_not_found, list_entries, parse_filters, read_entry, Filters};
use support::{connect, owner_url, remove_test_rows, service_database, ADMIN};
use uuid::Uuid;

/// Some tests here add rows to the shared `audit_log` and others count it, and
/// the test harness runs them on parallel threads. Each takes this turn first.
static TURN: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

fn filters(query: &str) -> Filters {
    parse_filters(query).unwrap()
}

/// Rows the owner counts for the same filters, the way the list must.
async fn owner_count(
    owner: &tokio_postgres::Client,
    entity: Option<&str>,
    entity_pk: Option<&str>,
    action: Option<&str>,
) -> i64 {
    owner
        .query_one(
            "SELECT count(*) FROM public.audit_log \
             WHERE ($1::text IS NULL OR entity = $1) \
               AND ($2::text IS NULL OR entity_pk = $2) \
               AND ($3::text IS NULL OR action = $3)",
            &[&entity, &entity_pk, &action],
        )
        .await
        .unwrap()
        .get(0)
}

#[tokio::test]
async fn each_filter_narrows_the_result_and_combinations_are_anded() {
    let Some(url) = owner_url() else { return };
    let _turn = TURN.lock().await;
    let owner = connect(&url).await;
    let db = service_database(&url);

    let all = list_entries(&db, &filters(""), 1, 100).await.unwrap();
    assert_eq!(all.1 as i64, owner_count(&owner, None, None, None).await);

    let campaigns = list_entries(&db, &filters("entity=campaign"), 1, 100)
        .await
        .unwrap();
    assert_eq!(
        campaigns.1 as i64,
        owner_count(&owner, Some("campaign"), None, None).await
    );
    assert!(campaigns.0.iter().all(|entry| entry.entity == "campaign"));
    assert!(campaigns.1 < all.1);

    let one = list_entries(&db, &filters("entity=campaign&entity_pk=1"), 1, 100)
        .await
        .unwrap();
    assert!(!one.0.is_empty() && one.0.iter().all(|entry| entry.entity_pk == "1"));
    assert_eq!(
        one.1 as i64,
        owner_count(&owner, Some("campaign"), Some("1"), None).await
    );

    let inserts = list_entries(&db, &filters("entity=app_user&action=INSERT"), 1, 100)
        .await
        .unwrap();
    assert_eq!(
        inserts.1 as i64,
        owner_count(&owner, Some("app_user"), None, Some("INSERT")).await
    );
    let updates = list_entries(&db, &filters("entity=app_user&action=DELETE"), 1, 100)
        .await
        .unwrap();
    assert_eq!(
        updates.1 as i64,
        owner_count(&owner, Some("app_user"), None, Some("DELETE")).await
    );

    let nobody = list_entries(
        &db,
        &filters("user_id=11111111-1111-1111-1111-0000000000ff"),
        1,
        100,
    )
    .await
    .unwrap();
    assert_eq!((nobody.0.len(), nobody.1), (0, 0));
}

#[tokio::test]
async fn time_bounds_are_inclusive_and_the_order_is_newest_first() {
    let Some(url) = owner_url() else { return };
    let _turn = TURN.lock().await;
    let owner = connect(&url).await;
    let db = service_database(&url);
    let key = format!("order-test-{}", Uuid::new_v4());
    let inserted = owner
        .query(
            "INSERT INTO public.audit_log (entity, entity_pk, action, data_after, executed_at) \
             VALUES ('role', $1, 'INSERT', '{}'::jsonb, TIMESTAMPTZ '2030-01-01 00:00:00+00'), \
                    ('role', $1, 'UPDATE', '{}'::jsonb, TIMESTAMPTZ '2030-01-01 00:00:00+00'), \
                    ('role', $1, 'DELETE', '{}'::jsonb, TIMESTAMPTZ '2030-01-02 00:00:00+00') \
             RETURNING audit_id",
            &[&key],
        )
        .await
        .unwrap();
    let ids: Vec<i64> = inserted.iter().map(|row| row.get(0)).collect();

    let both = format!("entity=role&entity_pk={key}");
    let everything = list_entries(&db, &filters(&both), 1, 10).await.unwrap();
    // executed_at descending, then audit_id descending.
    assert_eq!(
        everything.0.iter().map(|e| e.audit_id).collect::<Vec<_>>(),
        [ids[2], ids[1], ids[0]]
    );

    let from_exact = format!("{both}&from=2030-01-02T00:00:00Z");
    assert_eq!(
        list_entries(&db, &filters(&from_exact), 1, 10)
            .await
            .unwrap()
            .1,
        1
    );
    let to_exact = format!("{both}&to=2030-01-01T00:00:00Z");
    assert_eq!(
        list_entries(&db, &filters(&to_exact), 1, 10)
            .await
            .unwrap()
            .1,
        2
    );
    let instant = format!("{both}&from=2030-01-01T00:00:00Z&to=2030-01-01T00:00:00Z");
    assert_eq!(
        list_entries(&db, &filters(&instant), 1, 10)
            .await
            .unwrap()
            .1,
        2
    );

    let second = list_entries(&db, &filters(&both), 2, 2).await.unwrap();
    assert_eq!((second.0.len(), second.1), (1, 3));
    assert_eq!(second.0[0].audit_id, ids[0]);
    let past_the_end = list_entries(&db, &filters(&both), 9, 2).await.unwrap();
    assert_eq!((past_the_end.0.len(), past_the_end.1), (0, 3));

    remove_test_rows(&owner, &key).await;
}

#[tokio::test]
async fn one_entry_by_id_and_a_missing_one() {
    let Some(url) = owner_url() else { return };
    let _turn = TURN.lock().await;
    let db = service_database(&url);
    let (page, _) = list_entries(&db, &filters(""), 1, 1).await.unwrap();
    let newest = &page[0];
    assert_eq!(
        read_entry(&db, newest.audit_id).await.unwrap().as_ref(),
        Some(newest)
    );
    assert_eq!(read_entry(&db, i64::MAX).await.unwrap(), None);
    let error = audit_entry_not_found();
    assert_eq!(
        (error.status, error.code.as_str()),
        (404, "audit_entry_not_found")
    );
}

#[tokio::test]
async fn a_write_that_named_its_caller_is_attributed_to_it() {
    let Some(url) = owner_url() else { return };
    let _turn = TURN.lock().await;
    let mut owner = connect(&url).await;
    let db = service_database(&url);
    let transaction = owner.transaction().await.unwrap();
    // As every service's kit does, in the writer's own transaction.
    transaction
        .execute("SELECT set_config('mosaiq.user_id', $1, true)", &[&ADMIN])
        .await
        .unwrap();
    transaction
        .execute(
            "UPDATE public.campaign SET name = name WHERE campaign_id = $1",
            &[&1i32],
        )
        .await
        .unwrap();
    transaction.commit().await.unwrap();

    let named = list_entries(
        &db,
        &filters(&format!(
            "entity=campaign&entity_pk=1&user_id={ADMIN}&action=UPDATE"
        )),
        1,
        10,
    )
    .await
    .unwrap();
    assert!(!named.0.is_empty());
    let entry = &named.0[0];
    assert_eq!(
        entry.user_id.map(|id| id.to_string()).as_deref(),
        Some(ADMIN)
    );
    assert_eq!(
        (entry.entity.as_str(), entry.action.as_str()),
        ("campaign", "UPDATE")
    );
    assert!(entry.data_before.is_some() && entry.data_after.is_some());

    owner
        .execute(
            "DELETE FROM public.audit_log WHERE audit_id = $1",
            &[&entry.audit_id],
        )
        .await
        .unwrap();
}

#[tokio::test]
async fn a_credential_in_the_table_never_reaches_an_entry() {
    let Some(url) = owner_url() else { return };
    let _turn = TURN.lock().await;
    let owner = connect(&url).await;
    let db = service_database(&url);
    let key = format!("credential-test-{}", Uuid::new_v4());
    let id: i64 = owner
        .query_one(
            "INSERT INTO public.audit_log (entity, entity_pk, action, data_before, data_after) \
             VALUES ('app_user', $1, 'UPDATE', \
                     '{\"password_hash\":\"$argon2id$before\",\"name\":\"A\"}'::jsonb, \
                     '{\"password_hash\":\"$argon2id$after\",\"nested\":{\"password_hash\":\"x\",\"a\":[{\"password_hash\":\"y\"}]}}'::jsonb) \
             RETURNING audit_id",
            &[&key],
        )
        .await
        .unwrap()
        .get(0);

    let listed = list_entries(
        &db,
        &filters(&format!("entity=app_user&entity_pk={key}")),
        1,
        10,
    )
    .await
    .unwrap();
    let single = read_entry(&db, id).await.unwrap().unwrap();
    for entry in listed.0.iter().chain([&single]) {
        for payload in [&entry.data_before, &entry.data_after] {
            let text = payload.as_deref().unwrap();
            assert!(!text.contains("password_hash"), "{text}");
            assert!(!text.contains("argon2id"), "{text}");
        }
    }
    assert!(single.data_before.unwrap().contains("\"name\":\"A\""));

    remove_test_rows(&owner, &key).await;
}

#[tokio::test]
async fn the_service_reads_audit_log_and_holds_nothing_else() {
    let Some(url) = owner_url() else { return };
    let _turn = TURN.lock().await;
    let owner = connect(&url).await;
    let service = connect(&support::service_url(&url)).await;

    let has = |privileges: &'static str| {
        let owner = &owner;
        async move {
            owner
                .query_one(
                    "SELECT has_table_privilege('svc_audit_trail', 'public.audit_log', $1)",
                    &[&privileges],
                )
                .await
                .unwrap()
                .get::<_, bool>(0)
        }
    };
    assert!(has("SELECT").await);
    for write in [
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    ] {
        assert!(!has(write).await, "{write}");
    }

    // ADR-0034's audit-log query: it returns no row.
    let reaching = owner
        .query(
            "SELECT rolname FROM pg_roles WHERE rolname LIKE 'svc\\_%' \
             AND (has_table_privilege(oid, 'public.audit_log', 'INSERT, UPDATE, DELETE, TRUNCATE') \
                  OR (rolname <> 'svc_audit_trail' \
                      AND has_table_privilege(oid, 'public.audit_log', 'SELECT')))",
            &[],
        )
        .await
        .unwrap();
    assert!(reaching.is_empty());

    // Nothing is granted to the role by name but that one SELECT.
    let granted = owner
        .query(
            "SELECT c.relname::text, a.privilege_type \
             FROM pg_class AS c CROSS JOIN LATERAL aclexplode(c.relacl) AS a \
             WHERE a.grantee = 'svc_audit_trail'::regrole",
            &[],
        )
        .await
        .unwrap();
    let granted: Vec<(String, String)> = granted.iter().map(|r| (r.get(0), r.get(1))).collect();
    assert_eq!(granted, [("audit_log".to_owned(), "SELECT".to_owned())]);
    let functions: i64 = owner
        .query_one(
            "SELECT count(*) FROM pg_proc AS p CROSS JOIN LATERAL aclexplode(p.proacl) AS a \
             WHERE a.grantee = 'svc_audit_trail'::regrole",
            &[],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(functions, 0);

    // The role cannot alter the record, and reads no other table.
    for statement in [
        "INSERT INTO public.audit_log (entity, entity_pk, action) VALUES ('role', 'x', 'INSERT')",
        "UPDATE public.audit_log SET entity_pk = entity_pk",
        "DELETE FROM public.audit_log",
        "TRUNCATE public.audit_log",
        "SELECT 1 FROM public.campaign",
    ] {
        let error = service.execute(statement, &[]).await.unwrap_err();
        assert_eq!(
            error.code().map(|code| code.code()),
            Some("42501"),
            "{statement}"
        );
    }
}
