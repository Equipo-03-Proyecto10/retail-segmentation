//! Public response, value and error types.

use std::fmt;

use chrono::{DateTime, NaiveDate, TimeZone, Utc};
use indexmap::IndexMap;
use rust_decimal::Decimal;

/// One `{field, code}` member of the error envelope's `details`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Detail {
    pub field: String,
    pub code: String,
}

impl Detail {
    pub fn new(field: impl Into<String>, code: impl Into<String>) -> Self {
        Self {
            field: field.into(),
            code: code.into(),
        }
    }
}

/// An error that is safe to expose through the common error envelope.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ApiError {
    pub status: u16,
    pub code: String,
    pub message: String,
    pub details: Vec<Detail>,
    pub headers: Vec<(String, String)>,
}

impl ApiError {
    pub fn new(status: u16, code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            status,
            code: code.into(),
            message: message.into(),
            details: Vec::new(),
            headers: Vec::new(),
        }
    }

    pub fn with_details(mut self, details: Vec<Detail>) -> Self {
        self.details = details;
        self
    }

    pub fn with_header(mut self, name: impl Into<String>, value: impl Into<String>) -> Self {
        self.headers.push((name.into(), value.into()));
        self
    }

    pub(crate) fn invalid_token() -> Self {
        Self::new(401, "invalid_token", "The bearer token is invalid.")
    }

    pub(crate) fn malformed_body() -> Self {
        Self::new(
            400,
            "malformed_body",
            "The request body cannot be parsed safely.",
        )
    }

    pub(crate) fn invalid_body(details: Vec<Detail>) -> Self {
        Self::new(
            422,
            "invalid_body",
            "The request body violates the service schema.",
        )
        .with_details(details)
    }

    pub(crate) fn payload_too_large() -> Self {
        Self::new(413, "payload_too_large", "The request body exceeds 64 KiB.")
    }

    pub(crate) fn database_unavailable() -> Self {
        Self::new(
            503,
            "database_unavailable",
            "The service database is unavailable.",
        )
    }

    pub(crate) fn internal() -> Self {
        Self::new(
            500,
            "internal_error",
            "The service could not complete the request.",
        )
    }
}

impl fmt::Display for ApiError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} {}: {}", self.status, self.code, self.message)
    }
}

impl std::error::Error for ApiError {}

/// A value rendered to JSON and XML.
///
/// Timestamps are always time-zone aware, so a naive timestamp cannot be
/// rendered. `Money` is a two-place string; a bare `Decimal` stays a number.
/// `JsonText` keeps JSON source text: JSON replies parse valid text into its
/// value, while XML replies carry the original text. Invalid JSON is rendered
/// as a JSON string, so constructing and rendering a value remain infallible.
#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Decimal(Decimal),
    Money(String),
    Text(String),
    /// JSON source rendered as its parsed value in JSON and as text in XML.
    /// Invalid JSON is rendered as a JSON string instead of being rejected.
    JsonText(String),
    DateTime(DateTime<Utc>),
    Date(NaiveDate),
    List(Vec<Value>),
    Object(IndexMap<String, Value>),
}

impl Value {
    /// Build an object from `(key, value)` pairs, keeping their order.
    pub fn object<K, V, I>(pairs: I) -> Self
    where
        K: Into<String>,
        V: Into<Value>,
        I: IntoIterator<Item = (K, V)>,
    {
        Value::Object(
            pairs
                .into_iter()
                .map(|(key, value)| (key.into(), value.into()))
                .collect(),
        )
    }
}

/// Return a two-place money value, rounding half to even as kit-py does.
pub fn money(value: impl Into<Decimal>) -> Value {
    let mut amount = value.into().round_dp(2);
    amount.rescale(2);
    Value::Money(amount.to_string())
}

macro_rules! value_from_int {
    ($($type:ty),*) => {
        $(impl From<$type> for Value {
            fn from(value: $type) -> Self {
                Value::Int(i64::from(value))
            }
        })*
    };
}

value_from_int!(i8, i16, i32, i64, u8, u16, u32);

impl From<bool> for Value {
    fn from(value: bool) -> Self {
        Value::Bool(value)
    }
}

impl From<f64> for Value {
    fn from(value: f64) -> Self {
        Value::Float(value)
    }
}

impl From<Decimal> for Value {
    fn from(value: Decimal) -> Self {
        Value::Decimal(value)
    }
}

impl From<&str> for Value {
    fn from(value: &str) -> Self {
        Value::Text(value.to_owned())
    }
}

impl From<String> for Value {
    fn from(value: String) -> Self {
        Value::Text(value)
    }
}

impl<Tz: TimeZone> From<DateTime<Tz>> for Value {
    fn from(value: DateTime<Tz>) -> Self {
        Value::DateTime(value.with_timezone(&Utc))
    }
}

impl From<NaiveDate> for Value {
    fn from(value: NaiveDate) -> Self {
        Value::Date(value)
    }
}

impl<T: Into<Value>> From<Option<T>> for Value {
    fn from(value: Option<T>) -> Self {
        value.map_or(Value::Null, Into::into)
    }
}

impl<T: Into<Value>> From<Vec<T>> for Value {
    fn from(value: Vec<T>) -> Self {
        Value::List(value.into_iter().map(Into::into).collect())
    }
}

impl From<IndexMap<String, Value>> for Value {
    fn from(value: IndexMap<String, Value>) -> Self {
        Value::Object(value)
    }
}

impl From<&serde_json::Value> for Value {
    fn from(value: &serde_json::Value) -> Self {
        match value {
            serde_json::Value::Null => Value::Null,
            serde_json::Value::Bool(flag) => Value::Bool(*flag),
            serde_json::Value::Number(number) => {
                if let Some(integer) = number.as_i64() {
                    Value::Int(integer)
                } else if let Ok(decimal) = number.to_string().parse::<Decimal>() {
                    Value::Decimal(decimal)
                } else {
                    Value::Float(number.as_f64().unwrap_or(f64::NAN))
                }
            }
            serde_json::Value::String(text) => Value::Text(text.clone()),
            serde_json::Value::Array(items) => Value::List(items.iter().map(Into::into).collect()),
            serde_json::Value::Object(members) => Value::Object(
                members
                    .iter()
                    .map(|(key, item)| (key.clone(), item.into()))
                    .collect(),
            ),
        }
    }
}

impl From<serde_json::Value> for Value {
    fn from(value: serde_json::Value) -> Self {
        Value::from(&value)
    }
}

/// One resource, rendered under the XML root `root`.
#[derive(Clone, Debug)]
pub struct Representation {
    pub root: String,
    pub data: IndexMap<String, Value>,
    pub status: u16,
    pub headers: Vec<(String, String)>,
    pub xsi_type: Option<String>,
}

impl Representation {
    pub fn new<K, V, I>(root: impl Into<String>, data: I) -> Self
    where
        K: Into<String>,
        V: Into<Value>,
        I: IntoIterator<Item = (K, V)>,
    {
        Self {
            root: root.into(),
            data: data
                .into_iter()
                .map(|(key, value)| (key.into(), value.into()))
                .collect(),
            status: 200,
            headers: Vec::new(),
            xsi_type: None,
        }
    }

    pub fn status(mut self, status: u16) -> Self {
        self.status = status;
        self
    }

    pub fn header(mut self, name: impl Into<String>, value: impl Into<String>) -> Self {
        self.headers.push((name.into(), value.into()));
        self
    }

    pub fn xsi_type(mut self, xsi_type: impl Into<String>) -> Self {
        self.xsi_type = Some(xsi_type.into());
        self
    }
}

/// An unpaged list: `{key: [...]}` in JSON, repeated `item` elements in XML.
#[derive(Clone, Debug)]
pub struct Collection {
    pub root: String,
    pub item: String,
    pub key: String,
    pub items: Vec<IndexMap<String, Value>>,
    pub status: u16,
    pub headers: Vec<(String, String)>,
}

impl Collection {
    pub fn new(
        root: impl Into<String>,
        item: impl Into<String>,
        key: impl Into<String>,
        items: Vec<IndexMap<String, Value>>,
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

/// A paged list with `page_info`. `size` defaults to the request's size.
#[derive(Clone, Debug)]
pub struct Page {
    pub root: String,
    pub item: String,
    pub key: String,
    pub items: Vec<IndexMap<String, Value>>,
    pub page: u32,
    pub total: u64,
    pub size: Option<u32>,
    pub status: u16,
    pub headers: Vec<(String, String)>,
}

impl Page {
    pub fn new(
        root: impl Into<String>,
        item: impl Into<String>,
        key: impl Into<String>,
        items: Vec<IndexMap<String, Value>>,
        page: u32,
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

    pub fn size(mut self, size: u32) -> Self {
        self.size = Some(size);
        self
    }
}

/// A 204 with optional headers.
#[derive(Clone, Debug, Default)]
pub struct NoContent {
    pub headers: Vec<(String, String)>,
}

/// What an operation handler returns.
#[derive(Clone, Debug)]
pub enum Reply {
    Representation(Representation),
    Collection(Collection),
    Page(Page),
    NoContent(NoContent),
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

impl From<NoContent> for Reply {
    fn from(value: NoContent) -> Self {
        Reply::NoContent(value)
    }
}
