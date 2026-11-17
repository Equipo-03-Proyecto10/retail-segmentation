//! The figure the web's `/reports/` shows, and the figure this service reports,
//! on the same database and the same data: they must be equal (F26-02, ADR-0062).
//!
//! `WEB_TOTALS` and `WEB_BY_STORE` are the web's own statements, word for word,
//! from `web/db/revenue_summary.py` (`_TOTALS`, `_BY_STORE`): the page's revenue
//! summary of the newest sale's window, read through `public.net_sale`. The
//! web is not imported here (ADR-0045: a service never imports `web/`);
//! `tests/test_analytics_kpis_reconciliation.py` at the repository root fails
//! when either statement stops being the web's, so this copy cannot drift. The
//! statements run as the database owner, as the web's `retail_app` would; the
//! service runs as `svc_kpis` through its own router.
//!
//! The sales recorded here are dated within the last days and carry reversals:
//! a partial return, a return in full and a return that leaves a sale one unit.

mod support;

use std::collections::BTreeMap;

use serde_json::Value;
use support::http::{send, Stack, TURN};
use support::{
    connect, customer, purge, remove_customer, remove_sales, reverse, sale, tag, Line, Spec,
};
use tokio_postgres::Client;

pub const WEB_TOTALS: &str = r#"SELECT max(t.occurred_at), count(*), coalesce(sum(t.net_total), 0), coalesce(bool_or(t.data_origin = 'SEEDED'), false) FROM net_sale AS t WHERE 
    t.occurred_at > (SELECT max(occurred_at) FROM net_sale)
                    - make_interval(days => %s)
"#;
pub const WEB_BY_STORE: &str = r#"SELECT s.name, count(*), sum(t.net_total) FROM net_sale AS t JOIN store AS s ON s.store_id = t.store_id WHERE 
    t.occurred_at > (SELECT max(occurred_at) FROM net_sale)
                    - make_interval(days => %s)
GROUP BY s.store_id, s.name ORDER BY sum(t.net_total) DESC, s.name"#;

/// The web's statements take the window as `%s`, which PostgreSQL spells `$1`.
fn as_postgres(statement: &str) -> String {
    statement.replace("%s", "$1::integer")
}

/// What the web's page shows for a window of `days`: revenue, transactions,
/// whether any is seeded, the newest sale, and the revenue and transactions of
/// each store by id.
struct WebFigures {
    revenue: String,
    transactions: i64,
    seeded: bool,
    until: String,
    by_store: BTreeMap<i32, (i64, String)>,
}

async fn web_figures(owner: &Client, days: i32) -> WebFigures {
    let totals = owner
        .query_one(
            &format!(
                "SELECT q.until::text, q.transactions, q.revenue::text, q.seeded \
                 FROM ({}) AS q(until, transactions, revenue, seeded)",
                as_postgres(WEB_TOTALS)
            ),
            &[&days],
        )
        .await
        .unwrap();
    let stores = owner
        .query(
            &format!(
                "SELECT s.store_id::integer, q.transactions, q.revenue::text \
                 FROM ({}) AS q(name, transactions, revenue) \
                 JOIN public.store AS s ON s.name = q.name",
                as_postgres(WEB_BY_STORE)
            ),
            &[&days],
        )
        .await
        .unwrap();
    WebFigures {
        until: totals.get(0),
        transactions: totals.get(1),
        revenue: totals.get(2),
        seeded: totals.get(3),
        by_store: stores
            .iter()
            .map(|row| (row.get(0), (row.get(1), row.get(2))))
            .collect(),
    }
}

async fn report(stack: &Stack, query: &str) -> Value {
    let token = stack.token("ADMIN");
    let path = format!("/v1/kpi-reports/net-sales?{query}");
    let (status, body) = send(&stack.app, "GET", &path, Some(&token), "application/json").await;
    assert_eq!(status, 200, "{body}");
    serde_json::from_str(&body).unwrap()
}

fn measure(row: &Value, code: &str) -> String {
    row["measures"]
        .as_array()
        .unwrap()
        .iter()
        .find(|measure| measure["code"] == code)
        .unwrap()["value"]
        .as_str()
        .unwrap()
        .to_owned()
}

/// The report over the web's window: the 700 days that end on the newest sale's
/// day, which no sale of the seed or of this test falls outside.
async fn same_window(stack: &Stack, owner: &Client, web: &WebFigures) -> (Value, Value) {
    let (from, to): (String, String) = {
        let row = owner
            .query_one(
                "SELECT (($1::text::timestamptz AT TIME ZONE 'UTC')::date - 700)::text, \
                        ($1::text::timestamptz AT TIME ZONE 'UTC')::date::text",
                &[&web.until],
            )
            .await
            .unwrap();
        (row.get(0), row.get(1))
    };
    let window = format!("from={from}&to={to}");
    (
        report(stack, &window).await,
        report(stack, &format!("{window}&group_by=store&size=100")).await,
    )
}

fn assert_reconciled(web: &WebFigures, total: &Value, by_store: &Value) {
    let row = &total["rows"][0];
    assert_eq!(measure(row, "net_amount"), web.revenue, "revenue");
    assert_eq!(
        measure(row, "net_orders"),
        web.transactions.to_string(),
        "transactions"
    );
    assert_eq!(total["data_origin"]["synthetic"], web.seeded, "seeded");
    let service: BTreeMap<i32, (i64, String)> = by_store["rows"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| {
            (
                row["store_id"].as_i64().unwrap() as i32,
                (
                    measure(row, "net_orders").parse().unwrap(),
                    measure(row, "net_amount"),
                ),
            )
        })
        .collect();
    assert_eq!(service, web.by_store, "by store");
}

#[tokio::test]
async fn the_report_equals_the_webs_reports_page_before_and_after_a_reversal() {
    let _turn = TURN.lock().await;
    let Some(stack) = Stack::start().await else {
        return;
    };
    let owner = connect(&stack.database).await;
    purge(&owner).await;
    let tag = tag();
    let buyer = customer(&owner).await;
    let line = |product, quantity, cents| Line {
        product,
        quantity,
        cents,
    };

    // The seed alone.
    let web = web_figures(&owner, 700).await;
    assert!(
        web.transactions > 0 && web.seeded,
        "the seed holds synthetic sales"
    );
    let (total, by_store) = same_window(&stack, &owner, &web).await;
    assert_reconciled(&web, &total, &by_store);

    // Three recent sales: one kept whole, one returned in part, one returned in full.
    let whole = sale(
        &owner,
        &tag,
        Spec::new(buyer, 1, 1, &recent(&owner, 2).await, "OBSERVED"),
        &[line(1, 2, 1999), line(2, 1, 501)],
    )
    .await;
    let partial = sale(
        &owner,
        &tag,
        Spec::new(buyer, 2, 3, &recent(&owner, 1).await, "OBSERVED"),
        &[line(1, 5, 1000)],
    )
    .await;
    let full = sale(
        &owner,
        &tag,
        Spec::new(buyer, 3, 2, &recent(&owner, 1).await, "OBSERVED"),
        &[line(2, 3, 700)],
    )
    .await;
    let before = web_figures(&owner, 700).await;
    assert_eq!(before.transactions, web.transactions + 3);
    let (total, by_store) = same_window(&stack, &owner, &before).await;
    assert_reconciled(&before, &total, &by_store);

    // Reversals lower both, and by the same amount. The sale returned in full
    // is neither a transaction nor revenue in either.
    reverse(&owner, partial, 1, 2).await;
    reverse(&owner, full, 2, 3).await;
    let after = web_figures(&owner, 700).await;
    assert_eq!(
        after.transactions,
        before.transactions - 1,
        "the web drops the sale"
    );
    assert_ne!(after.revenue, before.revenue);
    let (total, by_store) = same_window(&stack, &owner, &after).await;
    assert_reconciled(&after, &total, &by_store);
    assert_eq!(
        measure(&total["rows"][0], "net_orders"),
        after.transactions.to_string()
    );

    let _ = whole;
    remove_sales(&owner, &tag).await;
    remove_customer(&owner, buyer).await;
    purge(&owner).await;
    // The seed is as it was.
    let restored = web_figures(&owner, 700).await;
    assert_eq!(
        (restored.revenue, restored.transactions),
        (web.revenue, web.transactions)
    );
    stack.finish().await;
}

/// An instant `days` days ago, as an RFC 3339 text the database computed, so
/// the sale is never in the future.
async fn recent(owner: &Client, days: i32) -> String {
    owner
        .query_one(
            "SELECT to_char((now() - make_interval(days => $1::integer)) AT TIME ZONE 'UTC', \
                            'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')",
            &[&days],
        )
        .await
        .unwrap()
        .get(0)
}
