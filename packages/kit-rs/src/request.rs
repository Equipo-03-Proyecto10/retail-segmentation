//! Per-request mechanisms of kit-py's `core.py`: the correlation id, response
//! negotiation, the request media type, the 64 KiB cap and pagination.

use std::sync::LazyLock;

use axum::body::Body;
use bytes::Bytes;
use http::{HeaderMap, header};
use rand::RngCore;
use regex::Regex;

use crate::error::{ApiError, Detail};

/// The largest request body any service accepts.
pub const MAX_BODY_BYTES: usize = 65_536;

/// A response format: every service answers JSON and XML (ADR-0035).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Format {
    Json,
    Xml,
}

impl Format {
    pub fn media_type(self) -> &'static str {
        match self {
            Format::Json => "application/json",
            Format::Xml => "application/xml",
        }
    }
}

static CORRELATION: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[0-9A-Za-z-]{1,64}$").expect("valid pattern"));
static Q_VALUE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^-?[0-9]+(\.[0-9]+)?$").expect("valid pattern"));
static MIME_SPLIT: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"/|(?:[ \t]*;[ \t]*)").expect("valid pattern"));
static DIGITS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[0-9]+$").expect("valid pattern"));

/// The caller's id when it is a plain token of at most 64 characters,
/// otherwise 32 random hex characters (kit-py `secrets.token_hex(16)`).
pub(crate) fn correlation_id(headers: &HeaderMap) -> String {
    let supplied = headers
        .get("X-Correlation-ID")
        .and_then(|value| value.to_str().ok())
        .unwrap_or("");
    if CORRELATION.is_match(supplied) {
        return supplied.to_owned();
    }
    let mut bytes = [0u8; 16];
    rand::rng().fill_bytes(&mut bytes);
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// One parsed `Accept` item, as werkzeug's `MIMEAccept` holds it.
struct AcceptItem {
    value: String,
    quality: f64,
}

/// werkzeug's `parse_list_header`: commas outside quotes split items, quotes
/// around a whole item are removed, empty items are dropped.
fn list_items(value: &str) -> Vec<String> {
    let mut items = Vec::new();
    let mut item = String::new();
    let mut escape = false;
    let mut quote = false;
    for c in value.chars() {
        if escape {
            escape = false;
            item.push(c);
        } else if quote {
            match c {
                '\\' => {
                    escape = true;
                    item.push(c);
                }
                '"' => {
                    quote = false;
                    item.push(c);
                }
                c => item.push(c),
            }
        } else if c == ',' {
            items.push(std::mem::take(&mut item));
        } else if c == '"' {
            quote = true;
            item.push(c);
        } else {
            item.push(c);
        }
    }
    if quote {
        return Vec::new();
    }
    items.push(item);
    items
        .into_iter()
        .map(|item| {
            let item = item.trim();
            if item.len() >= 2 && item.starts_with('"') && item.ends_with('"') {
                item[1..item.len() - 1].to_owned()
            } else {
                item.to_owned()
            }
        })
        .filter(|item| !item.is_empty())
        .collect()
}

/// werkzeug's `parse_accept_header(value, MIMEAccept)`: an invalid `q` drops
/// the item; other parameters stay on the value, which then only matches a
/// server type with the same parameters.
fn parse_accept(value: &str) -> Vec<AcceptItem> {
    let mut items = Vec::new();
    for raw in list_items(value) {
        let mut parts = raw.split(';');
        let media = parts.next().unwrap_or("").trim().to_owned();
        let mut quality = 1.0;
        let mut options = Vec::new();
        let mut valid = true;
        for option in parts {
            let option = option.trim();
            if option.is_empty() {
                continue;
            }
            let (key, value) = option.split_once('=').unwrap_or((option, ""));
            let key = key.trim().to_ascii_lowercase();
            let value = value.trim().trim_matches('"').to_owned();
            if key == "q" {
                let q = value.trim_matches(|c| c == ' ' || c == '\t');
                match q.parse::<f64>() {
                    Ok(q) if Q_VALUE.is_match(value.trim()) && (0.0..=1.0).contains(&q) => {
                        quality = q;
                    }
                    _ => valid = false,
                }
            } else {
                options.push(format!("{key}={value}"));
            }
        }
        if !valid {
            continue;
        }
        let value = if options.is_empty() {
            media
        } else {
            format!("{media}; {}", options.join("; "))
        };
        items.push(AcceptItem { value, quality });
    }
    // Sorted by specificity, then quality, both descending; the sort is
    // stable, so equal items keep the client's order.
    items.sort_by(|a, b| {
        (specificity(&b.value), b.quality)
            .partial_cmp(&(specificity(&a.value), a.quality))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    items
}

fn mime_parts(value: &str) -> Vec<String> {
    MIME_SPLIT
        .split(&value.to_lowercase())
        .map(str::to_owned)
        .collect()
}

fn specificity(value: &str) -> Vec<bool> {
    MIME_SPLIT.split(value).map(|part| part != "*").collect()
}

/// werkzeug's `MIMEAccept._value_matches(server, client)`.
fn matches(server: &str, client: &str) -> bool {
    if !client.contains('/') {
        return false;
    }
    let server = mime_parts(server);
    let client = mime_parts(client);
    let (client_type, client_subtype) = (&client[0], client.get(1).map_or("", String::as_str));
    if client_type == "*" && client_subtype != "*" {
        return false;
    }
    let mut server_params = server[2..].to_vec();
    server_params.sort();
    let mut client_params = client.get(2..).map(<[String]>::to_vec).unwrap_or_default();
    client_params.sort();
    (client_type == "*" && client_subtype == "*")
        || (client_type == &server[0]
            && (client_subtype == "*"
                || (client_subtype == server[1] && client_params == server_params)))
}

fn quality(items: &[AcceptItem], server: &str) -> f64 {
    items
        .iter()
        .find(|item| matches(server, &item.value))
        .map_or(0.0, |item| item.quality)
}

/// werkzeug's `best_match(["application/json", "application/xml"])`.
fn best_match(items: &[AcceptItem]) -> Option<Format> {
    let mut result = None;
    let mut best_quality = -1.0;
    // werkzeug starts from (-1,), below any real specificity; None does too.
    let mut best_specificity: Option<Vec<bool>> = None;
    for (format, server) in [
        (Format::Json, "application/json"),
        (Format::Xml, "application/xml"),
    ] {
        let Some(item) = items.iter().find(|item| matches(server, &item.value)) else {
            continue;
        };
        let item_specificity = specificity(&item.value);
        if item.quality <= 0.0 || item.quality < best_quality {
            continue;
        }
        let more_specific = best_specificity
            .as_ref()
            .is_none_or(|best| &item_specificity > best);
        if item.quality > best_quality || more_specific {
            result = Some(format);
            best_quality = item.quality;
            best_specificity = Some(item_specificity);
        }
    }
    result
}

fn accept_header(headers: &HeaderMap) -> Option<&str> {
    headers
        .get(header::ACCEPT)
        .map(|value| value.to_str().unwrap_or("\u{fffd}"))
}

/// The format of an error raised before negotiation: XML only when the
/// client clearly prefers it (kit-py `_preferred_response_format`).
pub(crate) fn preferred_format(headers: &HeaderMap) -> Format {
    match accept_header(headers) {
        None => Format::Json,
        Some(accept) if accept.is_empty() || accept.trim() == "*/*" => Format::Json,
        Some(accept) => {
            let items = parse_accept(accept);
            match best_match(&items) {
                Some(Format::Xml) if quality(&items, "application/xml") > 0.0 => Format::Xml,
                _ => Format::Json,
            }
        }
    }
}

/// Strict negotiation for an operation: 406 when neither format is
/// acceptable (kit-py `_negotiate`).
pub(crate) fn negotiate(headers: &HeaderMap) -> Result<Format, ApiError> {
    let accept = match accept_header(headers) {
        None => return Ok(Format::Json),
        Some(accept) if accept.is_empty() || accept.trim() == "*/*" => return Ok(Format::Json),
        Some(accept) => accept,
    };
    let items = parse_accept(accept);
    match best_match(&items) {
        Some(format) if quality(&items, format.media_type()) > 0.0 => Ok(format),
        _ => Err(ApiError::new(
            406,
            "not_acceptable",
            "The requested response format is unsupported.",
        )),
    }
}

/// The request's media type without parameters, lower-cased, as werkzeug's
/// `request.mimetype`.
pub(crate) fn media_type(headers: &HeaderMap) -> String {
    headers
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .map(|value| value.split(';').next().unwrap_or("").trim().to_lowercase())
        .unwrap_or_default()
}

/// Read at most 64 KiB; a declared or actual larger body is a 413 before
/// anything else, authentication included.
pub(crate) async fn capped_body(headers: &HeaderMap, body: Body) -> Result<Bytes, ApiError> {
    let declared = headers
        .get(header::CONTENT_LENGTH)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.trim().parse::<u64>().ok());
    if declared.is_some_and(|length| length > MAX_BODY_BYTES as u64) {
        return Err(ApiError::payload_too_large());
    }
    axum::body::to_bytes(body, MAX_BODY_BYTES)
        .await
        .map_err(|_| ApiError::payload_too_large())
}

/// Validated `page` (from 1) and `size` (1 to 100, default 20); every bad
/// value is reported (kit-py `_pagination`).
pub(crate) fn pagination(query: Option<&str>) -> Result<(u64, u64), ApiError> {
    let first = |name: &str| {
        query.and_then(|query| {
            form_urlencoded::parse(query.as_bytes())
                .find(|(key, _)| key == name)
                .map(|(_, value)| value.into_owned())
        })
    };
    let mut details = Vec::new();
    let mut values = [1u64, 20u64];
    for (index, (name, default, minimum, maximum)) in
        [("page", 1u64, 1u64, None), ("size", 20, 1, Some(100u64))]
            .into_iter()
            .enumerate()
    {
        let raw = first(name);
        if let Some(raw) = &raw
            && !DIGITS.is_match(raw)
        {
            details.push(Detail::new(name, "invalid_type"));
            continue;
        }
        // Digits beyond u64 are out of range here; Python has no such limit,
        // but no page that far exists.
        let value = match raw {
            None => Some(default),
            Some(raw) => raw
                .parse::<u64>()
                .ok()
                .filter(|value| *value <= i64::MAX as u64),
        };
        match value {
            Some(value) if value >= minimum && maximum.is_none_or(|maximum| value <= maximum) => {
                values[index] = value;
            }
            _ => details.push(Detail::new(name, "out_of_range")),
        }
    }
    if !details.is_empty() {
        return Err(ApiError::new(
            422,
            "invalid_pagination",
            "Pagination values are outside their allowed ranges.",
        )
        .with_details(details));
    }
    Ok((values[0], values[1]))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn headers(accept: Option<&str>) -> HeaderMap {
        let mut headers = HeaderMap::new();
        if let Some(accept) = accept {
            headers.insert(header::ACCEPT, accept.parse().unwrap());
        }
        headers
    }

    /// The table werkzeug 3.1 produced for kit-py's two functions:
    /// (Accept, preferred, negotiated).
    const WERKZEUG: &[(Option<&str>, &str, &str)] = &[
        (None, "json", "json"),
        (Some(""), "json", "json"),
        (Some("*/*"), "json", "json"),
        (Some(" */* "), "json", "json"),
        (Some("application/json"), "json", "json"),
        (Some("application/xml"), "xml", "xml"),
        (Some("text/plain"), "json", "406"),
        (Some("application/*"), "json", "json"),
        (Some("text/*"), "json", "406"),
        (Some("application/xml, application/json"), "json", "json"),
        (Some("application/json, application/xml"), "json", "json"),
        (
            Some("application/xml;q=0.9, application/json"),
            "json",
            "json",
        ),
        (
            Some("application/xml, application/json;q=0.9"),
            "xml",
            "xml",
        ),
        (Some("application/json;q=0"), "json", "406"),
        (Some("application/json;q=0, */*"), "xml", "xml"),
        (Some("application/xml;q=0"), "json", "406"),
        (Some("*/*;q=0"), "json", "406"),
        (Some("application/*;q=0.5, application/xml"), "xml", "xml"),
        (
            Some("text/html, application/xml;q=0.9, */*;q=0.8"),
            "xml",
            "xml",
        ),
        (
            Some("text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"),
            "xml",
            "xml",
        ),
        (Some("APPLICATION/XML"), "xml", "xml"),
        (Some("application/xml;charset=utf-8"), "json", "406"),
        (
            Some("application/json;q=0.5, application/xml;q=0.5"),
            "json",
            "json",
        ),
        (
            Some("application/xml;q=0.5, application/json;q=0.5"),
            "json",
            "json",
        ),
        (Some("application/xml;q=1.0, */*;q=0.1"), "xml", "xml"),
        (Some("application/json;q=abc"), "json", "406"),
        (Some("application/xml;q=0.001"), "xml", "xml"),
        (Some("garbage"), "json", "406"),
        (Some("application/json, text/plain;q=0"), "json", "json"),
    ];

    fn name(format: Format) -> &'static str {
        match format {
            Format::Json => "json",
            Format::Xml => "xml",
        }
    }

    #[test]
    fn negotiation_matches_werkzeug() {
        for (accept, preferred, negotiated) in WERKZEUG {
            let headers = headers(*accept);
            assert_eq!(
                name(preferred_format(&headers)),
                *preferred,
                "preferred {accept:?}"
            );
            let got = match negotiate(&headers) {
                Ok(format) => name(format).to_owned(),
                Err(error) => error.status.to_string(),
            };
            assert_eq!(got, *negotiated, "negotiated {accept:?}");
        }
    }

    #[test]
    fn correlation_id_is_echoed_only_when_plain() {
        for (supplied, echoed) in [
            ("corr-123", true),
            ("bad value", false),
            ("", false),
            (&"x".repeat(65) as &str, false),
            (&"x".repeat(64) as &str, true),
        ] {
            let mut headers = HeaderMap::new();
            headers.insert("X-Correlation-ID", supplied.parse().unwrap());
            let id = correlation_id(&headers);
            assert_eq!(id == supplied, echoed, "{supplied:?}");
            assert!((1..=64).contains(&id.len()));
        }
        let generated = correlation_id(&HeaderMap::new());
        assert_eq!(generated.len(), 32);
        assert!(generated.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn pagination_matches_kit_py() {
        assert_eq!(pagination(None).unwrap(), (1, 20));
        assert_eq!(pagination(Some("page=2&size=7")).unwrap(), (2, 7));
        assert_eq!(pagination(Some("size=100&size=0")).unwrap(), (1, 100));
        for query in [
            "page=0",
            "page=no",
            "page=1_0",
            "page=%2B5",
            "page=%205",
            "page=+5",
            "size=0",
            "size=101",
            "size=no",
        ] {
            let error = pagination(Some(query)).unwrap_err();
            assert_eq!(
                (error.status, error.code.as_str()),
                (422, "invalid_pagination"),
                "{query}"
            );
        }
        let error = pagination(Some("page=0&size=x")).unwrap_err();
        assert_eq!(
            error.details,
            vec![
                Detail::new("page", "out_of_range"),
                Detail::new("size", "invalid_type")
            ]
        );
    }

    #[test]
    fn media_type_drops_parameters_and_case() {
        let mut headers = HeaderMap::new();
        headers.insert(
            header::CONTENT_TYPE,
            "Application/JSON; charset=UTF-8".parse().unwrap(),
        );
        assert_eq!(media_type(&headers), "application/json");
        assert_eq!(media_type(&HeaderMap::new()), "");
    }
}
