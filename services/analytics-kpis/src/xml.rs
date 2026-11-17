//! The XML of a report that carries members before its rows.
//!
//! kit-rs renders a list with `page_info` only as a `Page`, whose root holds
//! the items and nothing else, but `KpiReport` holds the report's own members
//! before `rows`, and the XSD ends it with `common:PageInfo`. The handler
//! therefore returns a plain representation whose `page_info` member the kit
//! writes as three unqualified elements; this layer rewrites exactly that
//! element into the common namespace, which is what the XSD requires and what
//! the contract's examples show. JSON is untouched.

use axum::body::{to_bytes, Body};
use axum::extract::Request;
use axum::http::{header, HeaderValue};
use axum::middleware::Next;
use axum::response::Response;

const OPEN: &str = "<page_info>";
const CLOSE: &str = "</page_info>";
/// A report page is at most a hundred rows of a few hundred bytes.
const LIMIT: usize = 4 * 1024 * 1024;

/// `<page_info><page>..` as `<common:PageInfo><common:page>..`; any other
/// document is returned as it is.
pub fn common_page_info(xml: &str) -> String {
    let (Some(start), Some(end)) = (xml.find(OPEN), xml.rfind(CLOSE)) else {
        return xml.to_owned();
    };
    if end < start {
        return xml.to_owned();
    }
    let mut inner = xml[start + OPEN.len()..end].to_owned();
    for member in ["page", "size", "total"] {
        inner = inner
            .replace(&format!("<{member}>"), &format!("<common:{member}>"))
            .replace(&format!("</{member}>"), &format!("</common:{member}>"));
    }
    format!(
        "{}<common:PageInfo>{}</common:PageInfo>{}",
        &xml[..start],
        inner,
        &xml[end + CLOSE.len()..]
    )
}

pub async fn common_page_info_layer(request: Request<Body>, next: Next) -> Response {
    let response = next.run(request).await;
    let is_xml = response
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| value.starts_with("application/xml"));
    if !is_xml {
        return response;
    }
    let (mut parts, body) = response.into_parts();
    let Ok(bytes) = to_bytes(body, LIMIT).await else {
        return Response::from_parts(parts, Body::empty());
    };
    let Ok(text) = std::str::from_utf8(&bytes) else {
        return Response::from_parts(parts, Body::from(bytes));
    };
    let rewritten = common_page_info(text);
    parts
        .headers
        .insert(header::CONTENT_LENGTH, HeaderValue::from(rewritten.len()));
    Response::from_parts(parts, Body::from(rewritten))
}
