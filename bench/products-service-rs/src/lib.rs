//! The bench-only Rust twin of products-service's reads (F15-01, ADR-0042,
//! ADR-0078).
//!
//! It answers every read of the products contract exactly as products-service
//! does: `GET /v1/products`, `GET /v1/products/{product_id}`, and
//! `GET /v1/products/{product_id}/details` and `.../relations`, with the same
//! filters and refusals, the same JSON and XML, the same envelope. It
//! implements no write: the contract's four writes pass the kit's checks like
//! any request and then answer 405, and any other method is the router's 405.
//! Like products-service in the benchmark it has no cache and no rate limiter,
//! and like every service it verifies the token and the session's liveness on
//! each request. It connects as `svc_products_bench`, which may read
//! `public.product` and the four tables of the products boundary (ADR-0078),
//! and nothing else.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::{Arc, OnceLock};

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
/// The two relation kinds of the contract, in the order of its enum.
const RELATION_KINDS: [&str; 2] = ["substitute", "complement"];
/// products-service's default for `PRODUCTS_PHOTO_BASE_URL`: the web's route
/// that serves `image_path`.
pub const DEFAULT_PHOTO_BASE_URL: &str = "http://localhost:5000/admin/products/image/";

/// The photo base URL, read once at start (`run`).
static PHOTO_BASE_URL: OnceLock<String> = OnceLock::new();

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

/// products-service's `_photo_url` quoting: Python's `urllib.parse.quote` with
/// `safe="/"`, which leaves letters, digits, `_.-~` and `/` and escapes every
/// other byte of the UTF-8 text.
fn quote(path: &str) -> String {
    let mut out = String::with_capacity(path.len());
    for byte in path.bytes() {
        if byte.is_ascii_alphanumeric() || b"_.-~/".contains(&byte) {
            out.push(char::from(byte));
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    out
}

/// The configured base, which must be an absolute http(s) URL, with exactly
/// one trailing slash; products-service refuses to start with anything else.
pub fn photo_base_url(value: Option<&str>) -> Result<String, String> {
    let value = value.unwrap_or(DEFAULT_PHOTO_BASE_URL).trim();
    let rest = value
        .strip_prefix("https://")
        .or_else(|| value.strip_prefix("http://"));
    match rest {
        Some(rest)
            if !rest.is_empty()
                && !rest
                    .chars()
                    .any(|character| character.is_whitespace() || "?#".contains(character)) =>
        {
            Ok(format!("{}/", value.trim_end_matches('/')))
        }
        _ => Err("PRODUCTS_PHOTO_BASE_URL must be an absolute http(s) URL.".to_owned()),
    }
}

/// The base joined to the web-managed `image_path`, or null with no photo.
pub fn photo_url(base_url: &str, image_path: Option<&str>) -> Value {
    match image_path {
        Some(path) if !path.is_empty() => {
            Value::from(format!("{base_url}{}", quote(path.trim_start_matches('/'))))
        }
        _ => Value::Null,
    }
}

/// The product with its commercial details, in the XSD's order, or
/// `product_not_found`. A product with no status row reads from `is_active`.
/// One statement, so the status, barcode and attributes share a snapshot.
pub async fn get_product_details(
    db: &Database,
    product_id: i64,
    photo_base_url: &str,
) -> Result<IndexMap<String, Value>, ApiError> {
    let id = i32::try_from(product_id).map_err(|_| not_found())?;
    let connection = db.connection().await?;
    let row = connection
        .query_opt(
            "SELECT p.product_id, p.sku, p.name, p.category_id, p.list_price, p.image_path, \
                    p.is_active, \
                    coalesce(s.commercial_status, \
                             CASE WHEN p.is_active THEN 'active' ELSE 'discontinued' END), \
                    b.barcode, \
                    array(SELECT a.name FROM products.product_attribute AS a \
                           WHERE a.product_id = p.product_id ORDER BY a.name), \
                    array(SELECT a.value FROM products.product_attribute AS a \
                           WHERE a.product_id = p.product_id ORDER BY a.name) \
             FROM public.product AS p \
             LEFT JOIN products.product_status AS s ON s.product_id = p.product_id \
             LEFT JOIN products.product_barcode AS b ON b.product_id = p.product_id \
             WHERE p.product_id = $1",
            &[&id],
        )
        .await?
        .ok_or_else(not_found)?;
    let list_price: Decimal = row.try_get(4)?;
    let names: Vec<String> = row.try_get(9)?;
    let values: Vec<String> = row.try_get(10)?;
    let attributes: Vec<Value> = names
        .into_iter()
        .zip(values)
        .map(|(name, value)| Value::object([("name", name), ("value", value)]))
        .collect();
    let image_path: Option<String> = row.try_get(5)?;
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
            "commercial_status".to_owned(),
            Value::from(row.try_get::<_, String>(7)?),
        ),
        ("attributes".to_owned(), Value::List(attributes)),
        (
            "photo_url".to_owned(),
            photo_url(photo_base_url, image_path.as_deref()),
        ),
        (
            "barcode".to_owned(),
            Value::from(row.try_get::<_, Option<String>>(8)?),
        ),
    ]))
}

/// `listProductRelations`' `kind`, as products-service reads it: the first
/// value, which must be one of the contract's two kinds.
pub fn parse_relation_kind(query: &str) -> Result<Option<String>, ApiError> {
    match first_values(query).remove("kind") {
        None => Ok(None),
        Some(kind) if RELATION_KINDS.contains(&kind.as_str()) => Ok(Some(kind)),
        Some(_) => Err(invalid_query("kind", "enum")),
    }
}

/// One page of a product's relations, ordered by kind and then related id,
/// and the filtered total; `product_not_found` for an unknown product.
pub async fn list_product_relations(
    db: &Database,
    product_id: i64,
    kind: Option<&str>,
    page: u32,
    size: u32,
) -> Result<(Vec<IndexMap<String, Value>>, u64), ApiError> {
    let id = i32::try_from(product_id).map_err(|_| not_found())?;
    let connection = db.connection().await?;
    connection
        .query_opt("SELECT 1 FROM public.product WHERE product_id = $1", &[&id])
        .await?
        .ok_or_else(not_found)?;
    let limit = i64::from(size);
    let offset = i64::from(page.saturating_sub(1)) * limit;
    let rows = connection
        .query(
            "SELECT related_product_id, relation_kind, count(*) OVER () \
             FROM products.product_relation \
             WHERE product_id = $1 AND ($2::text IS NULL OR relation_kind = $2) \
             ORDER BY relation_kind, related_product_id LIMIT $3 OFFSET $4",
            &[&id, &kind, &limit, &offset],
        )
        .await?;
    if let Some(first) = rows.first() {
        let total: i64 = first.try_get(2)?;
        let items = rows
            .iter()
            .map(|row| {
                Ok(IndexMap::from([
                    (
                        "related_product_id".to_owned(),
                        Value::from(row.try_get::<_, i32>(0)?),
                    ),
                    ("kind".to_owned(), Value::from(row.try_get::<_, String>(1)?)),
                ]))
            })
            .collect::<Result<Vec<_>, ApiError>>()?;
        return Ok((items, u64::try_from(total).unwrap_or(0)));
    }
    if offset == 0 {
        return Ok((Vec::new(), 0));
    }
    // A page past the end: the window count has no row to ride on.
    let total: i64 = connection
        .query_one(
            "SELECT count(*) FROM products.product_relation \
             WHERE product_id = $1 AND ($2::text IS NULL OR relation_kind = $2)",
            &[&id, &kind],
        )
        .await?
        .try_get(0)?;
    Ok((Vec::new(), u64::try_from(total).unwrap_or(0)))
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

async fn details(ctx: Ctx) -> Result<Reply, ApiError> {
    let id = ctx.path_i64("product_id").ok_or_else(not_found)?;
    let base = PHOTO_BASE_URL
        .get()
        .map_or(DEFAULT_PHOTO_BASE_URL, String::as_str);
    Ok(Representation::new(
        "ProductDetails",
        get_product_details(ctx.db(), id, base).await?,
    )
    .into())
}

async fn relations(ctx: Ctx) -> Result<Reply, ApiError> {
    let id = ctx.path_i64("product_id").ok_or_else(not_found)?;
    let query = REQUEST_QUERY.try_with(Clone::clone).unwrap_or_default();
    let kind = parse_relation_kind(&query)?;
    let (items, total) =
        list_product_relations(ctx.db(), id, kind.as_deref(), ctx.page, ctx.size).await?;
    Ok(Page::new(
        "ProductRelations",
        "ProductRelation",
        "product_relations",
        items,
        ctx.page,
        total,
    )
    .size(ctx.size)
    .into())
}

/// `ProductRelations` carries `product_id` before its items, and the kit's
/// `Page` renders the page alone (products-service has the same gap, in
/// `_lead_with_product_id`). This adds that one member to the 200 answer of
/// `listProductRelations`, from the path, in either format: a JSON member, or
/// the first element after the root's start tag.
async fn lead_with_product_id(request: Request<Body>, next: Next) -> Response {
    let product_id = relations_path_id(request.uri().path());
    let response = next.run(request).await;
    let Some(product_id) = product_id else {
        return response;
    };
    if response.status() != axum::http::StatusCode::OK {
        return response;
    }
    let (mut parts, body) = response.into_parts();
    let Ok(bytes) = axum::body::to_bytes(body, 1 << 20).await else {
        return Response::from_parts(parts, Body::empty());
    };
    let is_xml = parts
        .headers
        .get(axum::http::header::CONTENT_TYPE)
        .is_some_and(|value| value.as_bytes().starts_with(b"application/xml"));
    let text = String::from_utf8_lossy(&bytes);
    let led = if is_xml {
        insert_xml_product_id(&text, product_id)
    } else {
        insert_json_product_id(&text, product_id)
    };
    let body = led.unwrap_or_else(|| text.into_owned()).into_bytes();
    parts.headers.remove(axum::http::header::CONTENT_LENGTH);
    Response::from_parts(parts, Body::from(body))
}

/// `/v1/products/{id}/relations`, and only that: the product's id.
fn relations_path_id(path: &str) -> Option<i64> {
    let rest = path.strip_prefix("/v1/products/")?;
    let (id, tail) = rest.split_once('/')?;
    (tail == "relations" && digits(id)).then(|| id.parse().ok())?
}

fn insert_json_product_id(body: &str, product_id: i64) -> Option<String> {
    let mut document: serde_json::Value = serde_json::from_str(body).ok()?;
    document
        .as_object_mut()?
        .insert("product_id".to_owned(), product_id.into());
    serde_json::to_string(&document).ok()
}

fn insert_xml_product_id(body: &str, product_id: i64) -> Option<String> {
    let root = body.find("<ProductRelations")?;
    let end = root + body[root..].find('>')? + 1;
    Some(format!(
        "{}<product_id>{product_id}</product_id>{}",
        &body[..end],
        &body[end..]
    ))
}

/// The four writes of the contract: `createProduct`, `updateProduct`,
/// `updateProductDetails` and `replaceProductRelations`. The twin implements
/// no write
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
        let base = photo_base_url(std::env::var("PRODUCTS_PHOTO_BASE_URL").ok().as_deref())?;
        let _ = PHOTO_BASE_URL.set(base);
        let kit = Kit::new(SERVICE, SUFFIX, "contracts", config())?;
        let router = kit
            .operation("listProducts", list)?
            .operation("getProduct", detail)?
            .operation("createProduct", no_write)?
            .operation("updateProduct", no_write)?
            .operation("getProductDetails", details)?
            .operation("updateProductDetails", no_write)?
            .operation("listProductRelations", relations)?
            .operation("replaceProductRelations", no_write)?
            .ready_check(database_ready)
            .into_router()
            .layer(middleware::from_fn(lead_with_product_id))
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
    fn the_photo_base_is_an_absolute_url_with_one_trailing_slash() {
        assert_eq!(
            photo_base_url(None).unwrap(),
            "http://localhost:5000/admin/products/image/"
        );
        assert_eq!(
            photo_base_url(Some(" https://shop.example/photos// ")).unwrap(),
            "https://shop.example/photos/"
        );
        for bad in [
            "",
            "/relative/",
            "ftp://x/",
            "http://",
            "http://a b/",
            "https://a/?q=1",
        ] {
            assert!(photo_base_url(Some(bad)).is_err(), "{bad}");
        }
    }

    #[test]
    fn the_photo_url_is_the_base_and_the_quoted_web_path() {
        let base = "https://shop.example/photos/";
        assert_eq!(photo_url(base, None), Value::Null);
        assert_eq!(photo_url(base, Some("")), Value::Null);
        assert_eq!(
            photo_url(base, Some("/a b/1001-camera_é.webp")),
            Value::from("https://shop.example/photos/a%20b/1001-camera_%C3%A9.webp")
        );
        assert_eq!(quote("x~y.z-w_v/#?%"), "x~y.z-w_v/%23%3F%25");
    }

    #[test]
    fn the_relation_kind_is_one_of_the_two_and_the_first_value_wins() {
        assert_eq!(parse_relation_kind("").unwrap(), None);
        assert_eq!(
            parse_relation_kind("kind=complement&kind=x&size=5").unwrap(),
            Some("complement".to_owned())
        );
        for bad in [
            "kind=",
            "kind=Substitute",
            "kind=upsell",
            "kind=x&kind=substitute",
        ] {
            assert_eq!(
                refusal_of(parse_relation_kind(bad).unwrap_err()),
                refused(422, "invalid_query", "kind", "enum"),
                "{bad}"
            );
        }
    }

    fn refusal_of(error: ApiError) -> (u16, String, String, String) {
        let detail = error.details.first().cloned().unwrap();
        (error.status, error.code, detail.field, detail.code)
    }

    #[test]
    fn only_the_relations_path_of_a_product_is_led_with_its_id() {
        assert_eq!(relations_path_id("/v1/products/12/relations"), Some(12));
        for other in [
            "/v1/products/12",
            "/v1/products/12/details",
            "/v1/products/12/relations/substitute",
            "/v1/products/x/relations",
            "/v1/products//relations",
            "/v1/products/12/relations/",
            "/health/live",
        ] {
            assert_eq!(relations_path_id(other), None, "{other}");
        }
    }

    #[test]
    fn product_id_is_added_to_the_json_and_the_xml_page() {
        let json = r#"{"page_info":{"page":1,"size":20,"total":0},"product_relations":[]}"#;
        let led: serde_json::Value =
            serde_json::from_str(&insert_json_product_id(json, 7).unwrap()).unwrap();
        assert_eq!(led["product_id"], 7);
        assert_eq!(led["page_info"]["total"], 0);
        assert!(insert_json_product_id("[]", 7).is_none());
        let xml = "<?xml version='1.0' encoding='UTF-8'?>\n<ProductRelations xmlns=\"u\" \
                   xmlns:common=\"c\"><ProductRelation/></ProductRelations>";
        assert_eq!(
            insert_xml_product_id(xml, 7).unwrap(),
            "<?xml version='1.0' encoding='UTF-8'?>\n<ProductRelations xmlns=\"u\" \
             xmlns:common=\"c\"><product_id>7</product_id><ProductRelation/></ProductRelations>"
        );
        assert!(insert_xml_product_id("<Other/>", 7).is_none());
    }

    #[test]
    fn the_rate_limiter_is_off_whatever_the_environment() {
        assert!(!config().rate_limit_enabled);
    }
}
