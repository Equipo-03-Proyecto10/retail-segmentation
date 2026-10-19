//! One representation rendered to JSON and XML, as kit-py's test_rendering.py.

mod common;

use axum::body::Body;
use axum::http::Response;
use chrono::{FixedOffset, NaiveDate, TimeZone};
use common::*;
use http_body_util::BodyExt;
use indexmap::IndexMap;
use mosaiq_kit::rendering::render_reply;
use mosaiq_kit::{money, Collection, Format, NoContent, Page, Reply, Representation, Value};
use rust_decimal::Decimal;
use serde_json::json;

const NAMESPACE: &str = "https://example.invalid/value";

async fn render(reply: impl Into<Reply>, format: Format) -> (Response<()>, Vec<u8>) {
    let response: Response<Body> = render_reply(&reply.into(), format, NAMESPACE, 20);
    let (parts, body) = response.into_parts();
    let bytes = body.collect().await.unwrap().to_bytes().to_vec();
    (Response::from_parts(parts, ()), bytes)
}

fn json_of(bytes: &[u8]) -> serde_json::Value {
    serde_json::from_slice(bytes).unwrap()
}

fn items(ids: &[i64]) -> Vec<IndexMap<String, Value>> {
    ids.iter()
        .map(|id| IndexMap::from([("id".to_owned(), Value::Int(*id))]))
        .collect()
}

#[tokio::test]
async fn money_is_explicit_and_bare_decimal_remains_a_number() {
    assert_eq!(money(Decimal::new(12, 1)), Value::Money("1.20".to_owned()));
    assert_eq!(money(Decimal::new(125, 3)), Value::Money("0.12".to_owned()));
    assert_eq!(money(Decimal::new(135, 3)), Value::Money("0.14".to_owned()));
    assert_eq!(money(7), Value::Money("7.00".to_owned()));
    let (_, body) = render(
        Representation::new(
            "Value",
            [
                ("amount", money(Decimal::new(12, 1))),
                ("coordinate", Value::from(Decimal::new(125, 2))),
            ],
        ),
        Format::Json,
    )
    .await;
    assert_eq!(
        json_of(&body),
        json!({"amount": "1.20", "coordinate": 1.25})
    );
}

#[tokio::test]
async fn timestamps_are_utc_rfc3339_and_dates_are_full_dates() {
    let at = FixedOffset::west_opt(4 * 3600)
        .unwrap()
        .with_ymd_and_hms(2026, 10, 15, 8, 30, 4)
        .unwrap()
        + chrono::Duration::microseconds(123_456);
    let whole = FixedOffset::east_opt(0)
        .unwrap()
        .with_ymd_and_hms(2026, 10, 15, 12, 30, 0)
        .unwrap();
    let (_, body) = render(
        Representation::new(
            "Value",
            [
                ("at", Value::from(at)),
                ("whole", Value::from(whole)),
                (
                    "day",
                    Value::from(NaiveDate::from_ymd_opt(2026, 10, 15).unwrap()),
                ),
            ],
        ),
        Format::Json,
    )
    .await;
    assert_eq!(
        json_of(&body),
        json!({
            "at": "2026-10-15T12:30:04.123456Z",
            "whole": "2026-10-15T12:30:00Z",
            "day": "2026-10-15",
        })
    );
}

#[tokio::test]
async fn absent_value_is_null_in_json_and_omitted_in_xml() {
    let value = Representation::new(
        "Value",
        [("present", Value::from("yes")), ("absent", Value::Null)],
    );
    let (_, json_body) = render(value.clone(), Format::Json).await;
    let (_, xml_body) = render(value, Format::Xml).await;
    assert_eq!(json_of(&json_body)["absent"], serde_json::Value::Null);
    let xml = String::from_utf8(xml_body).unwrap();
    assert!(
        xml.contains("<present>yes</present>") && !xml.contains("absent"),
        "{xml}"
    );
}

#[tokio::test]
async fn text_is_escaped_in_xml() {
    let (_, body) = render(
        Representation::new("Value", [("title", Value::from("a<b & \"c\""))]),
        Format::Xml,
    )
    .await;
    let xml = String::from_utf8(body).unwrap();
    assert!(
        xml.contains("<title>a&lt;b &amp; &quot;c&quot;</title>"),
        "{xml}"
    );
}

#[tokio::test]
async fn collection_and_page_shapes() {
    let (_, collection) = render(
        Collection::new("Values", "Value", "values", items(&[1, 2])),
        Format::Json,
    )
    .await;
    assert_eq!(
        json_of(&collection),
        json!({"values": [{"id": 1}, {"id": 2}]})
    );
    let (_, page) = render(
        Page::new("Values", "Value", "values", items(&[1]), 2, 15).size(10),
        Format::Xml,
    )
    .await;
    let xml = String::from_utf8(page).unwrap();
    assert_eq!(xml.matches("<Value>").count(), 1, "{xml}");
    assert!(
        xml.contains(
            "<common:PageInfo><common:page>2</common:page><common:size>10</common:size>\
             <common:total>15</common:total></common:PageInfo>"
        ),
        "{xml}"
    );
}

#[tokio::test]
async fn empty_xml_page_uses_the_requested_size_and_validates() {
    let Some(app) = app().await else { return };
    let answer = call("GET", "/v1/notes?page=2&size=7")
        .bearer("valid_reader")
        .header("Accept", "application/xml")
        .send(&app)
        .await;
    assert!(answer.text().contains("<common:size>7</common:size>"));
    xsd_valid(
        &contracts_dir().join("kit-example/kit-example.xsd"),
        &answer.body,
    )
    .unwrap();
}

#[tokio::test]
async fn xml_errors_validate_as_the_common_error() {
    let Some(app) = app().await else { return };
    let answer = call("POST", "/v1/notes")
        .bearer("valid_writer")
        .header("Accept", "application/xml")
        .body("application/json", "{")
        .send(&app)
        .await;
    assert_common_xml(&answer, "malformed_body");
    let answer = call("POST", "/v1/notes")
        .bearer("valid_writer")
        .header("Accept", "application/xml")
        .json(json!({"title": ""}))
        .send(&app)
        .await;
    assert_eq!(answer.status, 422);
    assert_common_xml(&answer, "invalid_body");
    assert!(answer
        .text()
        .contains("<details><field>amount</field><code>required</code></details>"));
}

#[tokio::test]
async fn xsi_type_is_attached_to_the_root() {
    let (_, body) = render(
        Representation::new("Value", [("id", 1)]).xsi_type("SpecialValue"),
        Format::Xml,
    )
    .await;
    let xml = String::from_utf8(body).unwrap();
    assert!(
        xml.contains(
            r#"xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="SpecialValue""#
        ),
        "{xml}"
    );
}

#[tokio::test]
async fn no_content_is_204() {
    let (response, body) = render(NoContent::default(), Format::Json).await;
    assert_eq!(response.status(), 204);
    assert!(body.is_empty());
    assert!(response.headers().get("content-type").is_none());
}
