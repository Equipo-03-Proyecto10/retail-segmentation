//! Responses byte for byte as kit-py renders them (`rendering.py`). Each
//! expected body was produced by kit-py 0.1.1 from the same values.

mod common;

use axum::Router;
use chrono::{TimeZone, Utc};
use common::{get, operation, send, staged_contracts};
use http::Method;
use mosaiq_kit::{
    ApiError, Collection, Context, Detail, Kit, Map, Page, Reply, Representation, Value, money,
};
use rust_decimal::Decimal;

const DECLARATION: &str = "<?xml version='1.0' encoding='UTF-8'?>\n";
const ROOT_NS: &str = "xmlns=\"https://mosaiq.maxthecoder.online/xml/kit-example/v1\" \
                       xmlns:common=\"https://mosaiq.maxthecoder.online/xml/common/v1\"";

/// One GET operation answering `reply`, and the contracts it keeps alive.
fn serving(reply: Result<Reply, ApiError>) -> (Router, tempfile::TempDir) {
    let contracts = staged_contracts();
    let kit = Kit::builder("kit-example", "kit_test")
        .contracts_dir(contracts.path())
        .build()
        .unwrap();
    let mut listed = operation("listNotes", Method::GET, "/v1/notes");
    listed.paginated = true;
    let router = kit
        .routes()
        .route_operation(listed, move |_context: Context| {
            let reply = reply.clone();
            async move { reply }
        })
        .into_router();
    (router, contracts)
}

async fn body(reply: Result<Reply, ApiError>, accept: &str, query: &str) -> (u16, String) {
    let (router, _contracts) = serving(reply);
    let answer = send(
        &router,
        get(&format!("/v1/notes{query}"), &[("accept", accept)]),
    )
    .await;
    (answer.status, answer.text())
}

fn note() -> Map {
    let mut data = Map::new();
    data.insert("note_id".into(), Value::Int(1));
    data.insert("title".into(), "a<b&c\"'".into());
    data.insert("amount".into(), money(Decimal::new(25, 1)));
    data.insert("price".into(), Value::Decimal(Decimal::new(150, 2)));
    data.insert("flag".into(), Value::Bool(true));
    data.insert("none".into(), Value::Null);
    data.insert("tags".into(), vec!["x", "y"].into());
    let mut nested = Map::new();
    nested.insert("a".into(), Value::Int(1));
    data.insert("nested".into(), Value::Map(nested));
    data.insert(
        "at".into(),
        Utc.with_ymd_and_hms(2026, 1, 2, 3, 4, 5).unwrap().into(),
    );
    data.insert(
        "d".into(),
        chrono::NaiveDate::from_ymd_opt(2026, 1, 2).unwrap().into(),
    );
    data.insert("f".into(), Value::Float(2.5));
    data
}

#[tokio::test]
async fn representation_matches_kit_py() {
    let reply = Ok(Representation::new("Note", note()).into());
    assert_eq!(
        body(reply.clone(), "application/json", "").await.1,
        r#"{"note_id":1,"title":"a<b&c\"'","amount":"2.50","price":1.5,"flag":true,"none":null,"tags":["x","y"],"nested":{"a":1},"at":"2026-01-02T03:04:05Z","d":"2026-01-02","f":2.5}"#
    );
    assert_eq!(
        body(reply, "application/xml", "").await.1,
        format!(
            "{DECLARATION}<Note {ROOT_NS}><note_id>1</note_id><title>a&lt;b&amp;c\"'</title>\
             <amount>2.50</amount><price>1.50</price><flag>true</flag><tags>x</tags><tags>y</tags>\
             <nested><a>1</a></nested><at>2026-01-02T03:04:05Z</at><d>2026-01-02</d><f>2.5</f></Note>"
        )
    );
}

#[tokio::test]
async fn xsi_type_and_empty_values_match_kit_py() {
    let mut data = Map::new();
    data.insert("a".into(), Value::Int(1));
    let mut typed = Representation::new("Note", data);
    typed.xsi_type = Some("NoteV2".into());
    assert_eq!(
        body(Ok(typed.into()), "application/xml", "").await.1,
        format!(
            "{DECLARATION}<Note {ROOT_NS} xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\" \
             xsi:type=\"NoteV2\"><a>1</a></Note>"
        )
    );
    let mut data = Map::new();
    data.insert("e".into(), "".into());
    data.insert("m".into(), Value::Map(Map::new()));
    data.insert("l".into(), Value::List(Vec::new()));
    data.insert("cr".into(), "a\rb".into());
    data.insert("gt".into(), "a>b".into());
    assert_eq!(
        body(
            Ok(Representation::new("N", data).into()),
            "application/xml",
            ""
        )
        .await
        .1,
        format!("{DECLARATION}<N {ROOT_NS}><e></e><m/><cr>a&#13;b</cr><gt>a&gt;b</gt></N>")
    );
    assert_eq!(
        body(
            Ok(Representation::new("N", Map::new()).into()),
            "application/xml",
            ""
        )
        .await
        .1,
        format!("{DECLARATION}<N {ROOT_NS}/>")
    );
}

#[tokio::test]
async fn page_and_collection_match_kit_py() {
    let mut item = Map::new();
    item.insert("note_id".into(), Value::Int(1));
    let page: Reply = Page::new("Notes", "Note", "notes", vec![item.clone()], 2, 45).into();
    assert_eq!(
        body(Ok(page.clone()), "application/json", "?size=10")
            .await
            .1,
        r#"{"notes":[{"note_id":1}],"page_info":{"page":2,"size":10,"total":45}}"#
    );
    assert_eq!(
        body(Ok(page), "application/xml", "?size=10").await.1,
        format!(
            "{DECLARATION}<Notes {ROOT_NS}><Note><note_id>1</note_id></Note><common:PageInfo>\
             <common:page>2</common:page><common:size>10</common:size><common:total>45</common:total>\
             </common:PageInfo></Notes>"
        )
    );
    let collection: Reply = Collection::new("Notes", "Note", "notes", vec![item]).into();
    assert_eq!(
        body(Ok(collection), "application/json", "").await.1,
        r#"{"notes":[{"note_id":1}]}"#
    );
}

#[tokio::test]
async fn numbers_and_text_match_kit_py() {
    let mut data = Map::new();
    data.insert(
        "t".into(),
        Utc.with_ymd_and_hms(2026, 1, 2, 3, 4, 5)
            .unwrap()
            .checked_add_signed(chrono::Duration::milliseconds(123))
            .unwrap()
            .into(),
    );
    data.insert(
        "big".into(),
        Value::Decimal("12345678901234567890.123".parse().unwrap()),
    );
    data.insert("neg".into(), Value::Decimal(Decimal::new(-5, 1)));
    data.insert("zero".into(), Value::Decimal(Decimal::ZERO));
    data.insert("u".into(), "ñé€".into());
    assert_eq!(
        body(
            Ok(Representation::new("N", data).into()),
            "application/json",
            ""
        )
        .await
        .1,
        r#"{"t":"2026-01-02T03:04:05.123000Z","big":1.2345678901234567e+19,"neg":-0.5,"zero":0.0,"u":"\u00f1\u00e9\u20ac"}"#
    );
    let mut data = Map::new();
    data.insert(
        "big".into(),
        Value::Decimal("12345678901234567890.123".parse().unwrap()),
    );
    data.insert("f".into(), Value::Float(1e20));
    assert_eq!(
        body(
            Ok(Representation::new("N", data).into()),
            "application/xml",
            ""
        )
        .await
        .1,
        format!(
            "{DECLARATION}<N {ROOT_NS}><big>12345678901234567890.123</big>\
             <f>100000000000000000000</f></N>"
        )
    );
}

#[tokio::test]
async fn error_envelope_matches_kit_py() {
    let error = ApiError::new(
        422,
        "invalid_body",
        "The request body violates the service schema.",
    )
    .with_details(vec![
        Detail::new("name", "required"),
        Detail::new("x", "too_long"),
    ]);
    let (router, _contracts) = serving(Err(error));
    let answer = send(
        &router,
        get(
            "/v1/notes",
            &[("accept", "application/xml"), ("x-correlation-id", "abc")],
        ),
    )
    .await;
    assert_eq!(answer.status, 422);
    assert_eq!(
        answer.text(),
        format!(
            "{DECLARATION}<Error xmlns=\"https://mosaiq.maxthecoder.online/xml/common/v1\" \
             xmlns:common=\"https://mosaiq.maxthecoder.online/xml/common/v1\"><code>invalid_body</code>\
             <message>The request body violates the service schema.</message>\
             <correlation_id>abc</correlation_id><details><field>name</field><code>required</code>\
             </details><details><field>x</field><code>too_long</code></details></Error>"
        )
    );
    let answer = send(&router, get("/v1/notes", &[("x-correlation-id", "abc")])).await;
    assert_eq!(
        answer.text(),
        r#"{"code":"invalid_body","message":"The request body violates the service schema.","correlation_id":"abc","details":[{"field":"name","code":"required"},{"field":"x","code":"too_long"}]}"#
    );
}

#[tokio::test]
async fn no_content_has_no_body_and_no_type() {
    let (router, _contracts) = serving(Ok(Reply::NoContent(Vec::new())));
    let answer = send(&router, get("/v1/notes", &[])).await;
    assert_eq!(answer.status, 204);
    assert!(answer.body.is_empty());
    assert_eq!(answer.content_type(), None);
}
