//! analytics-kpis-service: reports the commercial KPIs of the platform under
//! their published definition, window and data origin, and only to the roles
//! and stores allowed to read them (RN-68; F26-02).
//!
//! The service is read-only: it owns no table, writes nothing, publishes no
//! stream and calls no other service. It reads `api_*` views other boundaries
//! publish (ADR-0044) and holds no privilege on a base table.
//!
//! This release serves the catalog (`listKpiDefinitions`) and net sales
//! (`getNetSalesReport`). The other reports of the contract are marked
//! `x-mosaiq-status: planned` (ADR-0073) until the stories that build them.

pub mod catalog;
pub mod net_sales;
pub mod query;
pub mod scope;
pub mod xml;

use std::sync::Arc;

use axum::body::Body;
use axum::extract::Request;
use axum::middleware::{self, Next};
use axum::response::Response;
use axum::Router;
use chrono::{DateTime, SubsecRound, Utc};
use indexmap::IndexMap;
use mosaiq_kit::{
    configure_logging, ApiError, Ctx, Database, Kit, KitConfig, Page, Reply, Representation, Value,
};

use crate::query::{parse_sales_query, SalesQuery};
use crate::scope::Scope;

pub const SERVICE: &str = "analytics-kpis";
pub const SUFFIX: &str = "kpis";

tokio::task_local! {
    static REQUEST: RequestInfo;
}

/// kit-rs gives a handler no query string, so the layer below keeps it.
#[derive(Clone, Default)]
struct RequestInfo {
    query: String,
}

fn query_string() -> String {
    REQUEST
        .try_with(|info| info.query.clone())
        .unwrap_or_default()
}

fn report_wire(
    query: &SalesQuery,
    scope: &Scope,
    generated_at: DateTime<Utc>,
    report: &net_sales::Report,
    page: u32,
    size: u32,
) -> IndexMap<String, Value> {
    let mut data: IndexMap<String, Value> = IndexMap::new();
    data.insert("kpi".into(), Value::from(net_sales::KPI));
    data.insert(
        "definition_version".into(),
        Value::from(net_sales::DEFINITION_VERSION),
    );
    data.insert("from".into(), Value::from(query.from));
    data.insert("to".into(), Value::from(query.to));
    data.insert(
        "granularity".into(),
        Value::from(query.granularity.map(|g| g.code())),
    );
    data.insert(
        "group_by".into(),
        Value::List(
            query
                .group_by
                .iter()
                .map(|dimension| Value::from(dimension.code()))
                .collect(),
        ),
    );
    data.insert("generated_at".into(), Value::from(generated_at));
    data.insert(
        "store_scope".into(),
        Value::object([
            ("restricted", Value::from(scope.restricted)),
            (
                "store_ids",
                if scope.restricted {
                    Value::from(scope.store_ids.clone())
                } else {
                    Value::List(Vec::new())
                },
            ),
        ]),
    );
    data.insert(
        "data_origin".into(),
        Value::object([
            (
                "views",
                Value::from(net_sales::origin_views(query, scope).to_vec()),
            ),
            ("synthetic", Value::from(report.synthetic)),
            // No published view carries the model version of a label, so no
            // window can be shown to span one (decisions to review in the PR).
            ("spans_model_versions", Value::from(false)),
            ("model_version_ids", Value::List(Vec::new())),
        ]),
    );
    data.insert(
        "rows".into(),
        Value::List(report.rows.iter().map(net_sales::Row::wire).collect()),
    );
    data.insert(
        "page_info".into(),
        Value::object([
            ("page", Value::from(page)),
            ("size", Value::from(size)),
            ("total", Value::from(report.total as i64)),
        ]),
    );
    data
}

async fn get_net_sales_report(ctx: Ctx) -> Result<Reply, ApiError> {
    let query = parse_sales_query(&query_string())?;
    let scope = scope::resolve(&ctx, query.store_id).await?;
    let generated_at = Utc::now().trunc_subsecs(0);
    let report = net_sales::read(ctx.db(), &query, &scope, ctx.page, ctx.size).await?;
    Ok(Representation::new(
        "KpiReport",
        report_wire(&query, &scope, generated_at, &report, ctx.page, ctx.size),
    )
    .into())
}

async fn list_kpi_definitions(ctx: Ctx) -> Result<Reply, ApiError> {
    let (items, total) = catalog::page(ctx.page, ctx.size)?;
    Ok(Page::new(
        "KpiDefinitions",
        "KpiDefinition",
        "kpi_definitions",
        items,
        ctx.page,
        total,
    )
    .size(ctx.size)
    .into())
}

/// Ready when every producer view the service reads can be read.
async fn views_ready(db: Arc<Database>) -> bool {
    let Ok(connection) = db.connection().await else {
        return false;
    };
    connection
        .query(
            "SELECT transaction_id FROM api_sales.net_purchase_line_v1 LIMIT $1",
            &[&0i64],
        )
        .await
        .is_ok()
        && connection
            .query(
                "SELECT customer_id FROM api_segments.current_label_v1 LIMIT $1",
                &[&0i64],
            )
            .await
            .is_ok()
        && connection
            .query(
                "SELECT user_id FROM api_users.store_assignment_v1 LIMIT $1",
                &[&0i64],
            )
            .await
            .is_ok()
}

async fn capture_request(request: Request<Body>, next: Next) -> Response {
    let info = RequestInfo {
        query: request.uri().query().unwrap_or_default().to_owned(),
    };
    REQUEST.scope(info, next.run(request)).await
}

pub fn router(config: KitConfig, contracts: &str) -> Result<Router, Box<dyn std::error::Error>> {
    Ok(Kit::new(SERVICE, SUFFIX, contracts, config)?
        .operation("getNetSalesReport", get_net_sales_report)?
        .operation("listKpiDefinitions", list_kpi_definitions)?
        .ready_check(views_ready)
        .into_router()
        .layer(middleware::from_fn(xml::common_page_info_layer))
        .layer(middleware::from_fn(capture_request)))
}

pub fn run() -> Result<(), Box<dyn std::error::Error>> {
    configure_logging(&std::env::var("LOG_LEVEL").unwrap_or_else(|_| "info".to_owned()));
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()?;
    runtime.block_on(async {
        let router = router(KitConfig::from_env(SUFFIX), "contracts")?;
        let listener = tokio::net::TcpListener::bind("0.0.0.0:5022").await?;
        mosaiq_kit::serve(listener, router).await?;
        Ok::<(), Box<dyn std::error::Error>>(())
    })
}
