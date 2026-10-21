//! The bench-only Rust twin of products-service's reads (F15-01, ADR-0042).
//!
//! It answers `GET /v1/products` and `GET /v1/products/{product_id}` exactly
//! as products-service does: the same filters and refusals, the same JSON and
//! XML, the same envelope. It implements no write: the contract's two writes
//! pass the kit's checks like any request and then answer 405, and any other
//! method is the router's 405. Like products-service in the benchmark it has no cache and no rate
//! limiter, and like every service it verifies the token and the session's
//! liveness on each request. It connects as `svc_products_bench`, which may
//! read `public.product` and nothing else.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::Arc;

use axum::body::Body;
use axum::extract::Request;
use axum::middleware::{self, Next};
use axum::response::Response;
use indexmap::IndexMap;
use mosaiq_kit::{
    configure_logging, money, ApiError, Ctx, Database, Detail, Kit, KitConfig, Page, Reply,
    Representation, Value,
};
use rust_decimal::Decimal;
use tokio_postgres::Row;

/// The contract it serves, byte for byte: contracts/products.
pub const SERVICE: &str = "products";
/// `svc_products_bench`, in PostgreSQL and in Redis.
pub const SUFFIX: &str = "products_bench";
pub const PORT: u16 = 5103;
/// products-service's `MAX_BATCH_IDS`.
pub const MAX_BATCH_IDS: usize = 50;
const MAX_Q: usize = 150;
const MAX_SKU: usize = 40;

tokio::task_local! {
    static REQUEST_QUERY: String;
}

/// `listProducts`' filters, as products-service reads them.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Filters {
    pub q: Option<String>,
    pub sku: Option<String>,
    pub category_id: Option<i16>,
    pub active: Option<bool>,
    pub ids: Option<Vec<i32>>,
}

fn invalid_query(field: &str, code: &str) -> ApiError {
    ApiError::new(
        422,
        "invalid_query",
        "A query parameter is outside its allowed range.",
    )
    .with_details(vec![Detail::new(field, code)])
}

fn not_found() -> ApiError {
    ApiError::new(
        404,
        "product_not_found",
        "No product has the requested product_id.",
    )
}

fn digits(value: &str) -> bool {
    !value.is_empty() && value.bytes().all(|byte| byte.is_ascii_digit())
}

/// Flask's `request.args.get`: the first value a name was given.
fn first_values(query: &str) -> HashMap<String, String> {
    let mut values = HashMap::new();
    for (key, value) in form_urlencoded::parse(query.as_bytes()) {
        values
            .entry(key.into_owned())
            .or_insert_with(|| value.into_owned());
    }
    values
}

fn text_filter(
    values: &HashMap<String, String>,
    name: &str,
    maximum: usize,
) -> Result<Option<String>, ApiError> {
    let Some(value) = values.get(name) else {
        return Ok(None);
    };
    let length = value.chars().count();
    if length == 0 {
        return Err(invalid_query(name, "too_short"));
    }
    if length > maximum {
        return Err(invalid_query(name, "too_long"));
    }
    Ok(Some(value.clone()))
}

fn category_filter(values: &HashMap<String, String>) -> Result<Option<i16>, ApiError> {
    let Some(value) = values.get("category_id") else {
        return Ok(None);
    };
    if !digits(value) {
        return Err(invalid_query("category_id", "invalid_type"));
    }
    match value.parse::<u64>() {
        Ok(number @ 1..=32_767) => Ok(Some(number as i16)),
        _ => Err(invalid_query("category_id", "out_of_range")),
    }
}

fn active_filter(values: &HashMap<String, String>) -> Result<Option<bool>, ApiError> {
    match values.get("active").map(String::as_str) {
        None => Ok(None),
        Some("true") => Ok(Some(true)),
        Some("false") => Ok(Some(false)),
        Some(_) => Err(invalid_query("active", "invalid_type")),
    }
}

fn ids_filter(values: &HashMap<String, String>) -> Result<Option<Vec<i32>>, ApiError> {
    let Some(value) = values.get("ids") else {
        return Ok(None);
    };
    // `[1-9][0-9]*(,[1-9][0-9]*)*`, products-service's pattern.
    let items: Vec<&str> = value.split(',').collect();
    if items
        .iter()
        .any(|item| !digits(item) || item.starts_with('0'))
    {
        return Err(invalid_query("ids", "invalid_format"));
    }
    if items.len() > MAX_BATCH_IDS {
        return Err(ApiError::new(
            422,
            "too_many_ids",
            "At most 50 product ids may be requested.",
        )
        .with_details(vec![Detail::new("ids", "max_items")]));
    }
    let mut ids = Vec::with_capacity(items.len());
    for item in items {
        match item.parse::<i32>() {
            Ok(id) => ids.push(id),
            Err(_) => return Err(invalid_query("ids", "out_of_range")),
        }
    }
    ids.sort_unstable();
    ids.dedup();
    Ok(Some(ids))
}

/// Read the filters in products-service's order, so the first refusal is the
/// same one it would give.
pub fn parse_filters(query: &str) -> Result<Filters, ApiError> {
    let values = first_values(query);
    Ok(Filters {
        q: text_filter(&values, "q", MAX_Q)?,
        sku: text_filter(&values, "sku", MAX_SKU)?,
        category_id: category_filter(&values)?,
        active: active_filter(&values)?,
        ids: ids_filter(&values)?,
    })
}

fn product(row: &Row) -> Result<IndexMap<String, Value>, ApiError> {
    let list_price: Decimal = row.try_get(4)?;
    Ok(IndexMap::from([
        (
            "product_id".to_owned(),
            Value::from(row.try_get::<_, i32>(0)?),
        ),
        ("sku".to_owned(), Value::from(row.try_get::<_, String>(1)?)),
        ("name".to_owned(), Value::from(row.try_get::<_, String>(2)?)),
        (
            "category_id".to_owned(),
            Value::from(row.try_get::<_, i16>(3)?),
        ),
        ("list_price".to_owned(), money(list_price)),
        (
            "is_active".to_owned(),
            Value::from(row.try_get::<_, bool>(6)?),
        ),
        (
            "image_path".to_owned(),
            Value::from(row.try_get::<_, Option<String>>(5)?),
        ),
    ]))
}

/// One page, ordered by `product_id`, and the filtered total.
pub async fn list_products(
    db: &Database,
    filters: &Filters,
    page: u32,
    size: u32,
) -> Result<(Vec<IndexMap<String, Value>>, u64), ApiError> {
    let connection = db.connection().await?;
    let limit = i64::from(size);
    let offset = i64::from(page.saturating_sub(1)) * limit;
    let rows = connection
        .query(
            "SELECT product_id, sku, name, category_id, list_price, image_path, is_active, \
             count(*) OVER () \
             FROM public.product \
             WHERE ($1::text IS NULL \
                    OR strpos(lower(name), lower($1)) > 0 OR strpos(lower(sku), lower($1)) > 0) \
               AND ($2::text IS NULL OR sku = $2) \
               AND ($3::int2 IS NULL OR category_id = $3) \
               AND ($4::bool IS NULL OR is_active = $4) \
               AND ($5::int4[] IS NULL OR product_id = ANY($5)) \
             ORDER BY product_id LIMIT $6 OFFSET $7",
            &[
                &filters.q,
                &filters.sku,
                &filters.category_id,
                &filters.active,
                &filters.ids,
                &limit,
                &offset,
            ],
        )
        .await?;
    if let Some(first) = rows.first() {
        let total: i64 = first.try_get(7)?;
        let items = rows.iter().map(product).collect::<Result<Vec<_>, _>>()?;
        return Ok((items, u64::try_from(total).unwrap_or(0)));
    }
    if offset == 0 {
        return Ok((Vec::new(), 0));
    }
    // A page past the end: the window count has no row to ride on.
    let total: i64 = connection
        .query_one(
            "SELECT count(*) FROM public.product \
             WHERE ($1::text IS NULL \
                    OR strpos(lower(name), lower($1)) > 0 OR strpos(lower(sku), lower($1)) > 0) \
               AND ($2::text IS NULL OR sku = $2) \
               AND ($3::int2 IS NULL OR category_id = $3) \
               AND ($4::bool IS NULL OR is_active = $4) \
               AND ($5::int4[] IS NULL OR product_id = ANY($5))",
            &[
                &filters.q,
                &filters.sku,
                &filters.category_id,
                &filters.active,
                &filters.ids,
            ],
        )
        .await?
        .try_get(0)?;
    Ok((Vec::new(), u64::try_from(total).unwrap_or(0)))
}

/// One product, or `product_not_found`.
pub async fn get_product(
    db: &Database,
    product_id: i64,
) -> Result<IndexMap<String, Value>, ApiError> {
    let id = i32::try_from(product_id).map_err(|_| not_found())?;
    let connection = db.connection().await?;
    let row = connection
        .query_opt(
            "SELECT product_id, sku, name, category_id, list_price, image_path, is_active \
             FROM public.product WHERE product_id = $1",
            &[&id],
        )
        .await?
        .ok_or_else(not_found)?;
    product(&row)
}

async fn list(ctx: Ctx) -> Result<Reply, ApiError> {
    let query = REQUEST_QUERY.try_with(Clone::clone).unwrap_or_default();
    let filters = parse_filters(&query)?;
    let (items, total) = list_products(ctx.db(), &filters, ctx.page, ctx.size).await?;
    Ok(
        Page::new("Products", "Product", "products", items, ctx.page, total)
            .size(ctx.size)
            .into(),
    )
}

async fn detail(ctx: Ctx) -> Result<Reply, ApiError> {
    let id = ctx.path_i64("product_id").ok_or_else(not_found)?;
    Ok(Representation::new("Product", get_product(ctx.db(), id).await?).into())
}

/// `createProduct` and `updateProduct`. The twin implements no write
/// (ADR-0042): a write reaches this only after the kit's own checks, the same
/// ones products-service makes (token, role, media type, size, body), and is
/// then refused with 405 without touching the database. Methods the contract
/// does not declare at all get the router's 405.
async fn no_write(_ctx: Ctx) -> Result<Reply, ApiError> {
    Err(ApiError::new(
        405,
        "method_not_allowed",
        "The bench twin serves the products reads only.",
    )
    .with_header("Allow", "GET"))
}

async fn database_ready(db: Arc<Database>) -> bool {
    let Ok(connection) = db.connection().await else {
        return false;
    };
    connection
        .query_opt("SELECT product_id FROM public.product LIMIT 1", &[])
        .await
        .is_ok()
}

/// kit-rs gives a handler no query string, so it is kept for the request.
async fn capture_query(request: Request<Body>, next: Next) -> Response {
    let query = request.uri().query().unwrap_or_default().to_owned();
    REQUEST_QUERY.scope(query, next.run(request)).await
}

/// The kit's configuration with the benchmark's fairness rule applied in code:
/// no rate limiter, whatever the environment says (ADR-0042, rule 2).
pub fn config() -> KitConfig {
    let mut config = KitConfig::from_env(SUFFIX);
    config.rate_limit_enabled = false;
    config
}

pub fn run() -> Result<(), Box<dyn std::error::Error>> {
    configure_logging(&std::env::var("LOG_LEVEL").unwrap_or_else(|_| "info".to_owned()));
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()?;
    runtime.block_on(async {
        let kit = Kit::new(SERVICE, SUFFIX, "contracts", config())?;
        let router = kit
            .operation("listProducts", list)?
            .operation("getProduct", detail)?
            .operation("createProduct", no_write)?
            .operation("updateProduct", no_write)?
            .ready_check(database_ready)
            .into_router()
            .layer(middleware::from_fn(capture_query));
        let listener = tokio::net::TcpListener::bind(("0.0.0.0", PORT)).await?;
        axum::serve(
            listener,
            router.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await?;
        Ok::<(), Box<dyn std::error::Error>>(())
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn refusal(query: &str) -> (u16, String, String, String) {
        let error = parse_filters(query).unwrap_err();
        let detail = error.details.first().cloned().unwrap();
        (error.status, error.code, detail.field, detail.code)
    }

    fn refused(
        status: u16,
        code: &str,
        field: &str,
        detail: &str,
    ) -> (u16, String, String, String) {
        (status, code.to_owned(), field.to_owned(), detail.to_owned())
    }

    #[test]
    fn no_query_is_no_filter() {
        assert_eq!(parse_filters("").unwrap(), Filters::default());
    }

    #[test]
    fn text_filters_count_characters_as_flask_does() {
        assert_eq!(
            refusal("q="),
            refused(422, "invalid_query", "q", "too_short")
        );
        assert_eq!(
            refusal(&format!("q={}", "é".repeat(151))),
            refused(422, "invalid_query", "q", "too_long")
        );
        assert!(parse_filters(&format!("q={}", "é".repeat(150))).is_ok());
        assert_eq!(
            refusal(&format!("sku={}", "x".repeat(41))),
            refused(422, "invalid_query", "sku", "too_long")
        );
        // The first value wins, as with request.args.get.
        assert_eq!(parse_filters("q=a&q=").unwrap().q.as_deref(), Some("a"));
    }

    #[test]
    fn category_and_active_follow_products_service() {
        assert_eq!(
            refusal("category_id=x"),
            refused(422, "invalid_query", "category_id", "invalid_type")
        );
        assert_eq!(
            refusal("category_id=-1"),
            refused(422, "invalid_query", "category_id", "invalid_type")
        );
        for value in ["0", "32768", "99999999999999999999999"] {
            assert_eq!(
                refusal(&format!("category_id={value}")),
                refused(422, "invalid_query", "category_id", "out_of_range")
            );
        }
        assert_eq!(parse_filters("category_id=7").unwrap().category_id, Some(7));
        assert_eq!(
            refusal("active=yes"),
            refused(422, "invalid_query", "active", "invalid_type")
        );
        assert_eq!(parse_filters("active=false").unwrap().active, Some(false));
    }

    #[test]
    fn ids_are_validated_deduplicated_and_sorted() {
        assert_eq!(
            parse_filters("ids=5,3,5,1").unwrap().ids,
            Some(vec![1, 3, 5])
        );
        for bad in ["", "0", "01", "1,,2", "1,a", "-1"] {
            assert_eq!(
                refusal(&format!("ids={bad}")),
                refused(422, "invalid_query", "ids", "invalid_format"),
                "{bad}"
            );
        }
        let fifty_one = (1..=51)
            .map(|id| id.to_string())
            .collect::<Vec<_>>()
            .join(",");
        assert_eq!(
            refusal(&format!("ids={fifty_one}")),
            refused(422, "too_many_ids", "ids", "max_items")
        );
        assert_eq!(
            refusal("ids=2147483648"),
            refused(422, "invalid_query", "ids", "out_of_range")
        );
    }

    #[test]
    fn refusals_come_in_products_services_order() {
        assert_eq!(
            refusal("ids=0&active=maybe&q="),
            refused(422, "invalid_query", "q", "too_short")
        );
    }

    #[test]
    fn the_rate_limiter_is_off_whatever_the_environment() {
        assert!(!config().rate_limit_enabled);
    }
}
