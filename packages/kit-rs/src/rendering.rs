//! JSON and XML rendering from one representation.

use axum::body::Body;
use axum::http::{header, HeaderName, HeaderValue, Response, StatusCode};
use chrono::{DateTime, SecondsFormat, Timelike, Utc};
use indexmap::IndexMap;
use quick_xml::escape::escape;

use crate::types::{ApiError, Detail, Reply, Value};

pub const COMMON_NAMESPACE: &str = "https://mosaiq.maxthecoder.online/xml/common/v1";
pub const XSI_NAMESPACE: &str = "http://www.w3.org/2001/XMLSchema-instance";
const XML_DECLARATION: &str = "<?xml version='1.0' encoding='UTF-8'?>\n";

/// The negotiated wire format.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Format {
    Json,
    Xml,
}

fn timestamp(value: &DateTime<Utc>) -> String {
    let format = if value.nanosecond() / 1_000 != 0 {
        SecondsFormat::Micros
    } else {
        SecondsFormat::Secs
    };
    value.to_rfc3339_opts(format, true)
}

/// Convert a value to its JSON wire form.
pub fn wire_value(value: &Value) -> serde_json::Value {
    match value {
        Value::Null => serde_json::Value::Null,
        Value::Bool(flag) => serde_json::Value::Bool(*flag),
        Value::Int(integer) => serde_json::Value::from(*integer),
        Value::Float(float) => serde_json::Value::from(*float),
        Value::Decimal(decimal) => {
            // A bare decimal is a JSON number, written as kit-py's float is.
            let normalized = decimal.normalize();
            let mut text = normalized.to_string();
            if normalized.scale() == 0 {
                text.push_str(".0");
            }
            serde_json::from_str(&text).unwrap_or(serde_json::Value::Null)
        }
        Value::Money(text) | Value::Text(text) => serde_json::Value::String(text.clone()),
        Value::JsonText(text) => {
            serde_json::from_str(text).unwrap_or_else(|_| serde_json::Value::String(text.clone()))
        }
        Value::DateTime(moment) => serde_json::Value::String(timestamp(moment)),
        Value::Date(date) => serde_json::Value::String(date.format("%Y-%m-%d").to_string()),
        Value::List(items) => serde_json::Value::Array(items.iter().map(wire_value).collect()),
        Value::Object(members) => serde_json::Value::Object(
            members
                .iter()
                .map(|(key, item)| (key.clone(), wire_value(item)))
                .collect(),
        ),
    }
}

fn wire_object(members: &IndexMap<String, Value>) -> serde_json::Value {
    serde_json::Value::Object(
        members
            .iter()
            .map(|(key, item)| (key.clone(), wire_value(item)))
            .collect(),
    )
}

fn text(value: &Value) -> String {
    match value {
        Value::Bool(flag) => if *flag { "true" } else { "false" }.to_owned(),
        Value::Int(integer) => integer.to_string(),
        Value::Float(float) => {
            let fixed = format!("{float:.6}");
            fixed.trim_end_matches('0').trim_end_matches('.').to_owned()
        }
        Value::Decimal(decimal) => decimal.to_string(),
        Value::Money(text) | Value::Text(text) | Value::JsonText(text) => text.clone(),
        Value::DateTime(moment) => timestamp(moment),
        Value::Date(date) => date.format("%Y-%m-%d").to_string(),
        Value::Null | Value::List(_) | Value::Object(_) => String::new(),
    }
}

/// XML 1.0 `Char`: tab, line feed, carriage return, and everything from U+0020
/// except the surrogates (not a Rust `char`) and U+FFFE and U+FFFF.
fn is_xml_char(character: char) -> bool {
    matches!(character, '\t' | '\n' | '\r' | '\u{20}'..='\u{D7FF}' | '\u{E000}'..='\u{FFFD}' | '\u{10000}'..='\u{10FFFF}')
}

/// Replace each character XML 1.0 cannot carry, even as a character reference,
/// with U+FFFD. JSON keeps the original text.
fn xml_safe(text: String) -> String {
    if text.chars().all(is_xml_char) {
        return text;
    }
    text.chars()
        .map(|character| {
            if is_xml_char(character) {
                character
            } else {
                '\u{FFFD}'
            }
        })
        .collect()
}

/// Writes elements in either the root's default namespace or the common one.
struct XmlWriter {
    out: String,
    default_namespace: String,
}

impl XmlWriter {
    fn open(name: &str, namespace: &str, xsi_type: Option<&str>) -> Self {
        let mut out = String::from(XML_DECLARATION);
        out.push('<');
        out.push_str(name);
        out.push_str(" xmlns=\"");
        out.push_str(&escape(namespace));
        out.push_str("\" xmlns:common=\"");
        out.push_str(COMMON_NAMESPACE);
        out.push('"');
        if let Some(xsi_type) = xsi_type {
            out.push_str(" xmlns:xsi=\"");
            out.push_str(XSI_NAMESPACE);
            out.push_str("\" xsi:type=\"");
            out.push_str(&escape(xsi_type));
            out.push('"');
        }
        out.push('>');
        Self {
            out,
            default_namespace: namespace.to_owned(),
        }
    }

    fn qualified(&self, name: &str, namespace: &str) -> String {
        if namespace == self.default_namespace {
            name.to_owned()
        } else {
            format!("common:{name}")
        }
    }

    fn start(&mut self, name: &str, namespace: &str) {
        let tag = self.qualified(name, namespace);
        self.out.push('<');
        self.out.push_str(&tag);
        self.out.push('>');
    }

    fn end(&mut self, name: &str, namespace: &str) {
        let tag = self.qualified(name, namespace);
        self.out.push_str("</");
        self.out.push_str(&tag);
        self.out.push('>');
    }

    fn append(&mut self, key: &str, value: &Value, namespace: &str) {
        match value {
            Value::Null => {}
            Value::List(items) => {
                for item in items {
                    self.append(key, item, namespace);
                }
            }
            Value::Object(members) => {
                self.start(key, namespace);
                self.members(members, namespace);
                self.end(key, namespace);
            }
            scalar => {
                self.start(key, namespace);
                self.out.push_str(&escape(xml_safe(text(scalar))));
                self.end(key, namespace);
            }
        }
    }

    fn members(&mut self, members: &IndexMap<String, Value>, namespace: &str) {
        for (key, item) in members {
            self.append(key, item, namespace);
        }
    }

    fn finish(mut self, name: &str) -> String {
        self.out.push_str("</");
        self.out.push_str(name);
        self.out.push('>');
        self.out
    }
}

fn response(
    body: Vec<u8>,
    status: u16,
    format: Format,
    headers: &[(String, String)],
) -> Response<Body> {
    let mut response = Response::new(Body::from(body));
    *response.status_mut() =
        StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    let content_type = match format {
        Format::Json => "application/json",
        Format::Xml => "application/xml",
    };
    response
        .headers_mut()
        .insert(header::CONTENT_TYPE, HeaderValue::from_static(content_type));
    add_headers(&mut response, headers);
    response
}

fn add_headers(response: &mut Response<Body>, headers: &[(String, String)]) {
    for (name, value) in headers {
        if let (Ok(name), Ok(value)) = (
            HeaderName::from_bytes(name.as_bytes()),
            HeaderValue::from_str(value),
        ) {
            response.headers_mut().insert(name, value);
        }
    }
}

fn json_bytes(value: &serde_json::Value) -> Vec<u8> {
    serde_json::to_vec(value).unwrap_or_default()
}

/// Render the XML body of a reply, or `None` for a 204.
pub fn render_xml(value: &Reply, namespace: &str, request_size: u32) -> Option<String> {
    match value {
        Reply::NoContent(_) => None,
        Reply::Representation(representation) => {
            let mut writer = XmlWriter::open(
                &representation.root,
                namespace,
                representation.xsi_type.as_deref(),
            );
            writer.members(&representation.data, namespace);
            Some(writer.finish(&representation.root))
        }
        Reply::Collection(collection) => {
            let mut writer = XmlWriter::open(&collection.root, namespace, None);
            for item in &collection.items {
                writer.append(&collection.item, &Value::Object(item.clone()), namespace);
            }
            Some(writer.finish(&collection.root))
        }
        Reply::Page(page) => {
            let size = page.size.unwrap_or(request_size);
            let mut writer = XmlWriter::open(&page.root, namespace, None);
            for item in &page.items {
                writer.append(&page.item, &Value::Object(item.clone()), namespace);
            }
            writer.start("PageInfo", COMMON_NAMESPACE);
            writer.append("page", &Value::from(page.page), COMMON_NAMESPACE);
            writer.append("size", &Value::from(size), COMMON_NAMESPACE);
            writer.append("total", &Value::Int(page.total as i64), COMMON_NAMESPACE);
            writer.end("PageInfo", COMMON_NAMESPACE);
            Some(writer.finish(&page.root))
        }
    }
}

/// Render the JSON body of a reply, or `None` for a 204.
pub fn render_json(value: &Reply, request_size: u32) -> Option<serde_json::Value> {
    match value {
        Reply::NoContent(_) => None,
        Reply::Representation(representation) => Some(wire_object(&representation.data)),
        Reply::Collection(collection) => {
            let items = collection.items.iter().map(wire_object).collect();
            Some(serde_json::json!({ collection.key.clone(): serde_json::Value::Array(items) }))
        }
        Reply::Page(page) => {
            let size = page.size.unwrap_or(request_size);
            let items = page.items.iter().map(wire_object).collect();
            Some(serde_json::json!({
                page.key.clone(): serde_json::Value::Array(items),
                "page_info": {"page": page.page, "size": size, "total": page.total},
            }))
        }
    }
}

/// Render a reply as an HTTP response.
pub fn render_reply(
    value: &Reply,
    format: Format,
    namespace: &str,
    request_size: u32,
) -> Response<Body> {
    let (status, headers) = match value {
        Reply::NoContent(no_content) => {
            let mut response = Response::new(Body::empty());
            *response.status_mut() = StatusCode::NO_CONTENT;
            add_headers(&mut response, &no_content.headers);
            return response;
        }
        Reply::Representation(item) => (item.status, &item.headers),
        Reply::Collection(item) => (item.status, &item.headers),
        Reply::Page(item) => (item.status, &item.headers),
    };
    let body = match format {
        Format::Json => render_json(value, request_size)
            .map(|json| json_bytes(&json))
            .unwrap_or_default(),
        Format::Xml => render_xml(value, namespace, request_size)
            .unwrap_or_default()
            .into_bytes(),
    };
    response(body, status, format, headers)
}

fn details_value(details: &[Detail]) -> Value {
    Value::List(
        details
            .iter()
            .map(|detail| {
                Value::object([
                    ("field", detail.field.as_str()),
                    ("code", detail.code.as_str()),
                ])
            })
            .collect(),
    )
}

/// Render the common error type.
pub fn render_error(error: &ApiError, correlation_id: &str, format: Format) -> Response<Body> {
    let body = match format {
        Format::Json => json_bytes(&serde_json::json!({
            "code": error.code,
            "message": error.message,
            "correlation_id": correlation_id,
            "details": wire_value(&details_value(&error.details)),
        })),
        Format::Xml => {
            let mut writer = XmlWriter::open("Error", COMMON_NAMESPACE, None);
            writer.append("code", &Value::from(error.code.as_str()), COMMON_NAMESPACE);
            writer.append(
                "message",
                &Value::from(error.message.as_str()),
                COMMON_NAMESPACE,
            );
            writer.append(
                "correlation_id",
                &Value::from(correlation_id),
                COMMON_NAMESPACE,
            );
            writer.append("details", &details_value(&error.details), COMMON_NAMESPACE);
            writer.finish("Error").into_bytes()
        }
    };
    response(body, error.status, format, &error.headers)
}

#[cfg(test)]
mod tests {
    use super::xml_safe;

    #[test]
    fn xml_safe_replaces_only_characters_xml_cannot_carry() {
        assert_eq!(
            xml_safe("a\u{1}b\u{FFFE}\u{FFFF}c".to_owned()),
            "a\u{FFFD}b\u{FFFD}\u{FFFD}c"
        );
        assert_eq!(
            xml_safe("tab\t\nline\r\u{FFFD}\u{10000}é".to_owned()),
            "tab\t\nline\r\u{FFFD}\u{10000}é"
        );
    }
}
