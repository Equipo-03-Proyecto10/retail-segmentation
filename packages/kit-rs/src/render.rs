//! JSON and XML responses from one representation (kit-py `rendering.py`).

use axum::body::Body;
use axum::response::Response;
use http::{HeaderName, HeaderValue, StatusCode, header};

use crate::error::{ApiError, Detail};
use crate::request::Format;
use crate::value::{Map, Value, write_json, write_json_map, xml_text};

pub const COMMON_NAMESPACE: &str = "https://mosaiq.maxthecoder.online/xml/common/v1";
const XSI_NAMESPACE: &str = "http://www.w3.org/2001/XMLSchema-instance";
const XML_DECLARATION: &str = "<?xml version='1.0' encoding='UTF-8'?>\n";

/// One resource: the XML root names it.
#[derive(Clone, Debug, PartialEq)]
pub struct Representation {
    pub root: String,
    pub data: Map,
    pub status: u16,
    pub headers: Vec<(String, String)>,
    /// `xsi:type` on the XML root, for a polymorphic resource.
    pub xsi_type: Option<String>,
}

impl Representation {
    pub fn new(root: impl Into<String>, data: Map) -> Self {
        Self {
            root: root.into(),
            data,
            status: 200,
            headers: Vec::new(),
            xsi_type: None,
        }
    }
}

/// An unpaged list: `{key: [...]}` in JSON, `<root><item/>...</root>` in XML.
#[derive(Clone, Debug, PartialEq)]
pub struct Collection {
    pub root: String,
    pub item: String,
    pub key: String,
    pub items: Vec<Map>,
    pub status: u16,
    pub headers: Vec<(String, String)>,
}

impl Collection {
    pub fn new(
        root: impl Into<String>,
        item: impl Into<String>,
        key: impl Into<String>,
        items: Vec<Map>,
    ) -> Self {
        Self {
            root: root.into(),
            item: item.into(),
            key: key.into(),
            items,
            status: 200,
            headers: Vec::new(),
        }
    }
}

/// A page with its `page_info` / `common:PageInfo`.
#[derive(Clone, Debug, PartialEq)]
pub struct Page {
    pub root: String,
    pub item: String,
    pub key: String,
    pub items: Vec<Map>,
    pub page: u64,
    pub total: u64,
    /// The page size; the request's validated size when `None`.
    pub size: Option<u64>,
    pub status: u16,
    pub headers: Vec<(String, String)>,
}

impl Page {
    pub fn new(
        root: impl Into<String>,
        item: impl Into<String>,
        key: impl Into<String>,
        items: Vec<Map>,
        page: u64,
        total: u64,
    ) -> Self {
        Self {
            root: root.into(),
            item: item.into(),
            key: key.into(),
            items,
            page,
            total,
            size: None,
            status: 200,
            headers: Vec::new(),
        }
    }
}

/// What an operation handler returns.
#[derive(Clone, Debug, PartialEq)]
pub enum Reply {
    Representation(Representation),
    Collection(Collection),
    Page(Page),
    /// 204 with no body and no `Content-Type`.
    NoContent(Vec<(String, String)>),
}

impl From<Representation> for Reply {
    fn from(value: Representation) -> Self {
        Reply::Representation(value)
    }
}

impl From<Collection> for Reply {
    fn from(value: Collection) -> Self {
        Reply::Collection(value)
    }
}

impl From<Page> for Reply {
    fn from(value: Page) -> Self {
        Reply::Page(value)
    }
}

/// A minimal XML writer whose bytes match lxml's `tostring()`: a default
/// namespace for the service, the `common` prefix for the common schema, and
/// lxml's escaping. An element with no text and no children is self-closing;
/// a scalar, even an empty one, always has its end tag, as lxml writes them.
struct Xml {
    out: String,
    namespace: String,
    /// The last start tag still lacks its `>`.
    pending: bool,
}

impl Xml {
    fn new(root: &str, namespace: &str, xsi_type: Option<&str>) -> Result<Self, ApiError> {
        let mut out = String::from(XML_DECLARATION);
        out.push('<');
        out.push_str(&checked_name(root)?);
        out.push_str(" xmlns=\"");
        out.push_str(&escape_attribute(namespace)?);
        out.push_str("\" xmlns:common=\"");
        out.push_str(COMMON_NAMESPACE);
        out.push('"');
        if let Some(xsi_type) = xsi_type {
            out.push_str(" xmlns:xsi=\"");
            out.push_str(XSI_NAMESPACE);
            out.push_str("\" xsi:type=\"");
            out.push_str(&escape_attribute(xsi_type)?);
            out.push('"');
        }
        Ok(Self {
            out,
            namespace: namespace.to_owned(),
            pending: true,
        })
    }

    /// An element in `namespace`: unprefixed in the default one, `common:` in
    /// the common one (lxml prefers the default when they are the same).
    fn qualified(&self, name: &str, namespace: &str) -> Result<String, ApiError> {
        let name = checked_name(name)?;
        Ok(if namespace == self.namespace {
            name
        } else {
            format!("common:{name}")
        })
    }

    fn settle(&mut self) {
        if self.pending {
            self.out.push('>');
            self.pending = false;
        }
    }

    fn open(&mut self, name: &str, namespace: &str) -> Result<String, ApiError> {
        let qualified = self.qualified(name, namespace)?;
        self.settle();
        self.out.push('<');
        self.out.push_str(&qualified);
        self.pending = true;
        Ok(qualified)
    }

    fn close(&mut self, qualified: &str) {
        if self.pending {
            self.out.push_str("/>");
            self.pending = false;
        } else {
            self.out.push_str("</");
            self.out.push_str(qualified);
            self.out.push('>');
        }
    }

    /// kit-py `_append()`: absent values are omitted, lists repeat the
    /// element, maps nest.
    fn append(&mut self, key: &str, value: &Value, namespace: &str) -> Result<(), ApiError> {
        match value {
            Value::Null => Ok(()),
            Value::List(items) => {
                for item in items {
                    self.append(key, item, namespace)?;
                }
                Ok(())
            }
            Value::Map(map) => {
                let qualified = self.open(key, namespace)?;
                for (child, item) in map {
                    self.append(child, item, namespace)?;
                }
                self.close(&qualified);
                Ok(())
            }
            scalar => {
                let text = escape_text(&xml_text(scalar))?;
                let qualified = self.open(key, namespace)?;
                self.settle();
                self.out.push_str(&text);
                self.close(&qualified);
                Ok(())
            }
        }
    }

    fn finish(mut self, root: &str) -> String {
        self.close(root);
        self.out
    }
}

/// lxml refuses characters XML cannot carry; kit-py then answers 500.
fn xml_compatible(value: &str) -> Result<(), ApiError> {
    let valid = value.chars().all(|c| {
        matches!(c, '\t' | '\n' | '\r')
            || ('\u{20}'..='\u{d7ff}').contains(&c)
            || ('\u{e000}'..='\u{fffd}').contains(&c)
            || c >= '\u{10000}'
    });
    if valid {
        Ok(())
    } else {
        Err(ApiError::internal())
    }
}

fn checked_name(name: &str) -> Result<String, ApiError> {
    xml_compatible(name)?;
    if name.is_empty() || name.contains(|c: char| c.is_whitespace() || "<>&\"'/=".contains(c)) {
        return Err(ApiError::internal());
    }
    Ok(name.to_owned())
}

fn escape_text(value: &str) -> Result<String, ApiError> {
    xml_compatible(value)?;
    let mut out = String::with_capacity(value.len());
    for c in value.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '\r' => out.push_str("&#13;"),
            c => out.push(c),
        }
    }
    Ok(out)
}

fn escape_attribute(value: &str) -> Result<String, ApiError> {
    xml_compatible(value)?;
    let mut out = String::with_capacity(value.len());
    for c in value.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\n' => out.push_str("&#10;"),
            '\r' => out.push_str("&#13;"),
            '\t' => out.push_str("&#9;"),
            c => out.push(c),
        }
    }
    Ok(out)
}

fn response(body: String, status: u16, format: Format, headers: &[(String, String)]) -> Response {
    let mut response = Response::new(Body::from(body));
    *response.status_mut() =
        StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static(format.media_type()),
    );
    apply_headers(&mut response, headers);
    response
}

pub(crate) fn apply_headers(response: &mut Response, headers: &[(String, String)]) {
    for (name, value) in headers {
        if let (Ok(name), Ok(value)) = (
            HeaderName::from_bytes(name.as_bytes()),
            HeaderValue::from_str(value),
        ) {
            response.headers_mut().insert(name, value);
        }
    }
}

/// Render a handler's reply in the negotiated format.
pub(crate) fn render_reply(
    reply: &Reply,
    format: Format,
    namespace: &str,
    request_size: u64,
) -> Result<Response, ApiError> {
    match reply {
        Reply::NoContent(headers) => {
            let mut response = Response::new(Body::empty());
            *response.status_mut() = StatusCode::NO_CONTENT;
            apply_headers(&mut response, headers);
            Ok(response)
        }
        Reply::Representation(value) => {
            let body = match format {
                Format::Json => {
                    let mut out = String::new();
                    write_json_map(&mut out, &value.data);
                    out
                }
                Format::Xml => {
                    let mut xml = Xml::new(&value.root, namespace, value.xsi_type.as_deref())?;
                    for (key, item) in &value.data {
                        xml.append(key, item, namespace)?;
                    }
                    xml.finish(&value.root)
                }
            };
            Ok(response(body, value.status, format, &value.headers))
        }
        Reply::Collection(value) => {
            let body = render_list(
                &value.root,
                &value.item,
                &value.key,
                &value.items,
                None,
                format,
                namespace,
            )?;
            Ok(response(body, value.status, format, &value.headers))
        }
        Reply::Page(value) => {
            let size = value.size.unwrap_or(request_size);
            let body = render_list(
                &value.root,
                &value.item,
                &value.key,
                &value.items,
                Some((value.page, size, value.total)),
                format,
                namespace,
            )?;
            Ok(response(body, value.status, format, &value.headers))
        }
    }
}

fn render_list(
    root: &str,
    item: &str,
    key: &str,
    items: &[Map],
    page_info: Option<(u64, u64, u64)>,
    format: Format,
    namespace: &str,
) -> Result<String, ApiError> {
    let as_int = |n: u64| Value::Int(i64::try_from(n).unwrap_or(i64::MAX));
    match format {
        Format::Json => {
            let mut data = Map::new();
            data.insert(
                key.to_owned(),
                Value::List(items.iter().cloned().map(Value::Map).collect()),
            );
            if let Some((page, size, total)) = page_info {
                let mut info = Map::new();
                info.insert("page".into(), as_int(page));
                info.insert("size".into(), as_int(size));
                info.insert("total".into(), as_int(total));
                data.insert("page_info".into(), Value::Map(info));
            }
            let mut out = String::new();
            write_json_map(&mut out, &data);
            Ok(out)
        }
        Format::Xml => {
            let mut xml = Xml::new(root, namespace, None)?;
            for member in items {
                let qualified = xml.open(item, namespace)?;
                for (child, value) in member {
                    xml.append(child, value, namespace)?;
                }
                xml.close(&qualified);
            }
            if let Some((page, size, total)) = page_info {
                let qualified = xml.open("PageInfo", COMMON_NAMESPACE)?;
                xml.append("page", &as_int(page), COMMON_NAMESPACE)?;
                xml.append("size", &as_int(size), COMMON_NAMESPACE)?;
                xml.append("total", &as_int(total), COMMON_NAMESPACE)?;
                xml.close(&qualified);
            }
            Ok(xml.finish(root))
        }
    }
}

/// The common error envelope.
pub(crate) fn render_error(error: &ApiError, correlation_id: &str, format: Format) -> Response {
    let body = match format {
        Format::Json => {
            let mut data = Map::new();
            data.insert("code".into(), Value::Text(error.code.clone()));
            data.insert("message".into(), Value::Text(error.message.clone()));
            data.insert(
                "correlation_id".into(),
                Value::Text(correlation_id.to_owned()),
            );
            data.insert(
                "details".into(),
                Value::List(error.details.iter().map(detail_value).collect()),
            );
            let mut out = String::new();
            write_json(&mut out, &Value::Map(data));
            out
        }
        Format::Xml => {
            let rendered = (|| -> Result<String, ApiError> {
                let mut xml = Xml::new("Error", COMMON_NAMESPACE, None)?;
                xml.append("code", &Value::Text(error.code.clone()), COMMON_NAMESPACE)?;
                xml.append(
                    "message",
                    &Value::Text(error.message.clone()),
                    COMMON_NAMESPACE,
                )?;
                xml.append(
                    "correlation_id",
                    &Value::Text(correlation_id.to_owned()),
                    COMMON_NAMESPACE,
                )?;
                for detail in &error.details {
                    xml.append("details", &detail_value(detail), COMMON_NAMESPACE)?;
                }
                Ok(xml.finish("Error"))
            })();
            match rendered {
                Ok(body) => body,
                // A message XML cannot carry: answer the generic error in JSON.
                Err(_) => return render_error(&ApiError::internal(), correlation_id, Format::Json),
            }
        }
    };
    response(body, error.status, format, &error.headers)
}

fn detail_value(detail: &Detail) -> Value {
    let mut map = Map::new();
    map.insert("field".into(), Value::Text(detail.field.clone()));
    map.insert("code".into(), Value::Text(detail.code.clone()));
    Value::Map(map)
}
