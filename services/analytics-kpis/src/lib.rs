//! analytics-kpis-service: reports the commercial KPIs of the platform under
//! their published definition, window and data origin, and only to the roles
//! and stores allowed to read them (RN-68; F26-02).
//!
//! The service is read-only: it owns no table, writes nothing, publishes no
//! stream and calls no other service. It reads `api_*` views other boundaries
//! publish (ADR-0044) and holds no privilege on a base table.
//!
//! This release serves the catalog (`listKpiDefinitions`), net sales
//! (`getNetSalesReport`), spend, retention and segment migrations
//! (`getSpendReport`, `getRetentionReport`, `getMigrationReport`, F26-03) and
//! demand by segment beside stock (`getDemandCoverageReport`). The other reports
//! of the contract are marked `x-mosaiq-status: planned` (ADR-0073) until the
//! stories that build them.

pub mod catalog;
pub mod demand_coverage;
pub mod migrations;
pub mod net_sales;
pub mod query;
pub mod retention;
pub mod scope;
pub mod spend;
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

use crate::query::{
    parse_demand_query, parse_migration_query, parse_retention_query, parse_sales_query,
    DemandQuery, Dimension, Granularity, MigrationQuery,
};
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

fn store_scope_wire(scope: &Scope) -> Value {
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
    ])
}

fn page_info_wire(page: u32, size: u32, total: u64) -> Value {
    Value::object([
        ("page", Value::from(page)),
        ("size", Value::from(size)),
        ("total", Value::from(total as i64)),
    ])
}

/// Everything a `KpiReport` says besides its rows.
struct Envelope<'a> {
    kpi: &'static str,
    definition_version: i64,
    from: chrono::NaiveDate,
    to: chrono::NaiveDate,
    granularity: Option<Granularity>,
    group_by: &'a [Dimension],
    scope: &'a Scope,
    generated_at: DateTime<Utc>,
    views: Vec<&'static str>,
    synthetic: bool,
    spans_model_versions: bool,
    model_version_ids: Vec<i64>,
    rows: Vec<Value>,
    total: u64,
}

fn report_wire(envelope: Envelope<'_>, page: u32, size: u32) -> IndexMap<String, Value> {
    let mut data: IndexMap<String, Value> = IndexMap::new();
    data.insert("kpi".into(), Value::from(envelope.kpi));
    data.insert(
        "definition_version".into(),
        Value::from(envelope.definition_version),
    );
    data.insert("from".into(), Value::from(envelope.from));
    data.insert("to".into(), Value::from(envelope.to));
    data.insert(
        "granularity".into(),
        Value::from(envelope.granularity.map(|g| g.code())),
    );
    data.insert(
        "group_by".into(),
        Value::List(
            envelope
                .group_by
                .iter()
                .map(|dimension| Value::from(dimension.code()))
                .collect(),
        ),
    );
    data.insert("generated_at".into(), Value::from(envelope.generated_at));
    data.insert("store_scope".into(), store_scope_wire(envelope.scope));
    data.insert(
        "data_origin".into(),
        Value::object([
            ("views", Value::from(envelope.views)),
            ("synthetic", Value::from(envelope.synthetic)),
            (
                "spans_model_versions",
                Value::from(envelope.spans_model_versions),
            ),
            ("model_version_ids", Value::from(envelope.model_version_ids)),
        ]),
    );
    data.insert("rows".into(), Value::List(envelope.rows));
    data.insert(
        "page_info".into(),
        page_info_wire(page, size, envelope.total),
    );
    data
}

async fn get_net_sales_report(ctx: Ctx) -> Result<Reply, ApiError> {
    let query = parse_sales_query(&query_string())?;
    let scope = scope::resolve(&ctx, query.store_id).await?;
    let generated_at = Utc::now().trunc_subsecs(0);
    let report = net_sales::read(ctx.db(), &query, &scope, ctx.page, ctx.size).await?;
    let envelope = Envelope {
        kpi: net_sales::KPI,
        definition_version: net_sales::DEFINITION_VERSION,
        from: query.from,
        to: query.to,
        granularity: query.granularity,
        group_by: &query.group_by,
        scope: &scope,
        generated_at,
        views: net_sales::origin_views(&query, &scope),
        synthetic: report.synthetic,
        // The labels of net sales are not yet read with their model version; the
        // spend and retention reports name them (F26-03).
        spans_model_versions: false,
        model_version_ids: Vec::new(),
        rows: report.rows.iter().map(net_sales::Row::wire).collect(),
        total: report.total,
    };
    Ok(Representation::new("KpiReport", report_wire(envelope, ctx.page, ctx.size)).into())
}

/// A sales report names the model versions of the labels it groups or filters by,
/// and marks the window when they are two or more (ADR-0064).
fn spans(versions: &[i64]) -> bool {
    versions.len() > 1
}

async fn get_spend_report(ctx: Ctx) -> Result<Reply, ApiError> {
    let query = parse_sales_query(&query_string())?;
    let scope = scope::resolve(&ctx, query.store_id).await?;
    let generated_at = Utc::now().trunc_subsecs(0);
    let report = spend::read(ctx.db(), &query, &scope, ctx.page, ctx.size).await?;
    let envelope = Envelope {
        kpi: spend::KPI,
        definition_version: spend::DEFINITION_VERSION,
        from: query.from,
        to: query.to,
        granularity: query.granularity,
        group_by: &query.group_by,
        scope: &scope,
        generated_at,
        views: spend::origin_views(&query, &scope),
        synthetic: report.synthetic,
        spans_model_versions: spans(&report.model_version_ids),
        rows: report.rows.iter().map(spend::Row::wire).collect(),
        model_version_ids: report.model_version_ids,
        total: report.total,
    };
    Ok(Representation::new("KpiReport", report_wire(envelope, ctx.page, ctx.size)).into())
}

async fn get_retention_report(ctx: Ctx) -> Result<Reply, ApiError> {
    let query = parse_retention_query(&query_string())?;
    let scope = scope::resolve(&ctx, query.store_id).await?;
    let generated_at = Utc::now().trunc_subsecs(0);
    let report =
        retention::read(ctx.db(), &query, &scope, generated_at, ctx.page, ctx.size).await?;
    let envelope = Envelope {
        kpi: retention::KPI,
        definition_version: retention::DEFINITION_VERSION,
        from: query.from,
        to: query.to,
        granularity: query.granularity,
        group_by: &query.group_by,
        scope: &scope,
        generated_at,
        views: retention::origin_views(&query, &scope),
        synthetic: report.synthetic,
        spans_model_versions: spans(&report.model_version_ids),
        rows: report.rows.iter().map(retention::Row::wire).collect(),
        model_version_ids: report.model_version_ids,
        total: report.total,
    };
    Ok(Representation::new("KpiReport", report_wire(envelope, ctx.page, ctx.size)).into())
}

async fn get_migration_report(ctx: Ctx) -> Result<Reply, ApiError> {
    let query: MigrationQuery = parse_migration_query(&query_string())?;
    // A change has no store, so no store-scoped role reads it: the contract's
    // roles keep them out, and the report covers every store.
    let scope = Scope {
        restricted: false,
        store_ids: Vec::new(),
        named: None,
    };
    let generated_at = Utc::now().trunc_subsecs(0);
    let report = migrations::read(ctx.db(), &query, ctx.page, ctx.size).await?;
    let envelope = Envelope {
        kpi: migrations::KPI,
        definition_version: migrations::DEFINITION_VERSION,
        from: query.from,
        to: query.to,
        granularity: query.granularity,
        group_by: &query.group_by,
        scope: &scope,
        generated_at,
        views: vec![migrations::CHANGE_VIEW],
        synthetic: report.synthetic,
        spans_model_versions: report.spans_model_versions,
        rows: report.rows.iter().map(migrations::Row::wire).collect(),
        model_version_ids: report.model_version_ids,
        total: report.total,
    };
    Ok(Representation::new("KpiReport", report_wire(envelope, ctx.page, ctx.size)).into())
}

fn demand_coverage_wire(
    query: &DemandQuery,
    scope: &Scope,
    generated_at: DateTime<Utc>,
    report: &demand_coverage::Report,
    page: u32,
    size: u32,
) -> IndexMap<String, Value> {
    let mut data: IndexMap<String, Value> = IndexMap::new();
    data.insert("from".into(), Value::from(query.from));
    data.insert("to".into(), Value::from(query.to));
    data.insert("window_days".into(), Value::from(query.window_days()));
    data.insert(
        "target_cover_days".into(),
        Value::from(query.target_cover_days),
    );
    data.insert("generated_at".into(), Value::from(generated_at));
    data.insert(
        "calculation".into(),
        Value::from(demand_coverage::CALCULATION),
    );
    data.insert("store_scope".into(), store_scope_wire(scope));
    data.insert(
        "data_origin".into(),
        Value::object([
            (
                "views",
                Value::from(demand_coverage::origin_views(scope).to_vec()),
            ),
            ("synthetic", Value::from(report.synthetic)),
            ("spans_model_versions", Value::from(false)),
            ("model_version_ids", Value::List(Vec::new())),
        ]),
    );
    data.insert(
        "rows".into(),
        Value::List(report.rows.iter().map(demand_coverage::Row::wire).collect()),
    );
    data.insert("page_info".into(), page_info_wire(page, size, report.total));
    data
}

async fn get_demand_coverage_report(ctx: Ctx) -> Result<Reply, ApiError> {
    let generated_at = Utc::now().trunc_subsecs(0);
    let query = parse_demand_query(&query_string(), generated_at.date_naive())?;
    let scope = scope::resolve(&ctx, query.store_id).await?;
    let report = demand_coverage::read(ctx.db(), &query, &scope, ctx.page, ctx.size).await?;
    Ok(Representation::new(
        "DemandCoverageReport",
        demand_coverage_wire(&query, &scope, generated_at, &report, ctx.page, ctx.size),
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
        && connection
            .query(
                "SELECT customer_id FROM api_segments.assignment_change_v1 LIMIT $1",
                &[&0i64],
            )
            .await
            .is_ok()
        && connection
            .query(
                "SELECT run_id FROM api_models.run_version_v1 LIMIT $1",
                &[&0i64],
            )
            .await
            .is_ok()
        && connection
            .query(
                "SELECT product_id FROM api_products.product_ref_v1 LIMIT $1",
                &[&0i64],
            )
            .await
            .is_ok()
        && connection
            .query(
                "SELECT product_id FROM api_stock.stock_level_v1 LIMIT $1",
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
        .operation("getSpendReport", get_spend_report)?
        .operation("getRetentionReport", get_retention_report)?
        .operation("getMigrationReport", get_migration_report)?
        .operation("getDemandCoverageReport", get_demand_coverage_report)?
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
