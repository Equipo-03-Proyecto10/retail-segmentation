//! Wire values, and their JSON and XML text exactly as kit-py writes them.
//!
//! kit-py serialises with `json.dumps(..., separators=(",", ":"))`, which
//! escapes every non-ASCII character and prints a float as Python's `repr`
//! does; a bare `Decimal` becomes a float, while `money()` is a two-place
//! string. The writers here reproduce those bytes, so a client sees the same
//! body from a Flask and a Rust service.

use std::fmt::Write as _;

use chrono::{DateTime, NaiveDate, TimeZone, Timelike, Utc};
use indexmap::IndexMap;
use rust_decimal::prelude::ToPrimitive;
use rust_decimal::{Decimal, RoundingStrategy};

/// An ordered object, as a Python dict keeps its insertion order.
pub type Map = IndexMap<String, Value>;

/// A value a representation carries.
#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    /// `null` in JSON; the element is omitted in XML.
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    /// A bare decimal: a JSON number, exact in XML.
    Decimal(Decimal),
    /// A two-place amount from [`money`]: a string on the wire.
    Money(String),
    Text(String),
    /// Always UTC on the wire (RFC 3339, `Z`); a naive time cannot be built.
    DateTime(DateTime<Utc>),
    Date(NaiveDate),
    /// Repeated elements in XML.
    List(Vec<Value>),
    Map(Map),
}

/// A decimal amount with an explicit two-place wire representation, rounded
/// half to even as Python's `Decimal.quantize` does.
pub fn money(value: impl Into<Decimal>) -> Value {
    let mut amount = value
        .into()
        .round_dp_with_strategy(2, RoundingStrategy::MidpointNearestEven);
    amount.rescale(2);
    Value::Money(amount.to_string())
}

impl From<bool> for Value {
    fn from(value: bool) -> Self {
        Value::Bool(value)
    }
}

macro_rules! from_int {
    ($($t:ty),*) => {$(
        impl From<$t> for Value {
            fn from(value: $t) -> Self {
                Value::Int(i64::from(value))
            }
        }
    )*};
}
from_int!(i8, i16, i32, i64, u8, u16, u32);

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

impl From<Map> for Value {
    fn from(value: Map) -> Self {
        Value::Map(value)
    }
}

/// Python's `datetime.isoformat()` in UTC: microseconds only when present.
pub(crate) fn datetime_text(value: &DateTime<Utc>) -> String {
    let micros = value.nanosecond() / 1_000 % 1_000_000;
    if micros == 0 {
        value.format("%Y-%m-%dT%H:%M:%SZ").to_string()
    } else {
        format!("{}.{micros:06}Z", value.format("%Y-%m-%dT%H:%M:%S"))
    }
}

pub(crate) fn date_text(value: &NaiveDate) -> String {
    value.format("%Y-%m-%d").to_string()
}

/// Python's `repr(float)`, as `json.dumps` prints it.
pub(crate) fn python_float(value: f64) -> String {
    if value.is_nan() {
        return "NaN".into();
    }
    if value.is_infinite() {
        return if value > 0.0 { "Infinity" } else { "-Infinity" }.into();
    }
    let sign = if value.is_sign_negative() { "-" } else { "" };
    if value == 0.0 {
        return format!("{sign}0.0");
    }
    // Rust's `{:e}` gives the same shortest round-trip digits Python uses.
    let scientific = format!("{:e}", value.abs());
    let (mantissa, exponent) = scientific
        .split_once('e')
        .expect("`{:e}` always has an exponent");
    let exponent: i32 = exponent.parse().expect("`{:e}` exponent is an integer");
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    let length = digits.len() as i32;
    if (-4..16).contains(&exponent) {
        let point = exponent + 1;
        let fixed = if point <= 0 {
            format!("0.{}{digits}", "0".repeat((-point) as usize))
        } else if point >= length {
            format!("{digits}{}.0", "0".repeat((point - length) as usize))
        } else {
            let (whole, fraction) = digits.split_at(point as usize);
            format!("{whole}.{fraction}")
        };
        format!("{sign}{fixed}")
    } else {
        let (first, rest) = digits.split_at(1);
        let rest = if rest.is_empty() {
            String::new()
        } else {
            format!(".{rest}")
        };
        let exponent_sign = if exponent < 0 { '-' } else { '+' };
        format!("{sign}{first}{rest}e{exponent_sign}{:02}", exponent.abs())
    }
}

/// A JSON string as `json.dumps` (`ensure_ascii=True`) writes it.
pub(crate) fn write_json_string(out: &mut String, value: &str) {
    out.push('"');
    for character in value.chars() {
        match character {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            // Everything outside ' '..='~' is escaped, DEL included.
            c if !(' '..='~').contains(&c) => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    let _ = write!(out, "\\u{unit:04x}");
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

pub(crate) fn write_json(out: &mut String, value: &Value) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Int(number) => {
            let _ = write!(out, "{number}");
        }
        Value::Float(number) => out.push_str(&python_float(*number)),
        // wire_value() turns a bare Decimal into a float.
        Value::Decimal(number) => out.push_str(&python_float(number.to_f64().unwrap_or(f64::NAN))),
        Value::Money(text) | Value::Text(text) => write_json_string(out, text),
        Value::DateTime(moment) => write_json_string(out, &datetime_text(moment)),
        Value::Date(day) => write_json_string(out, &date_text(day)),
        Value::List(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                write_json(out, item);
            }
            out.push(']');
        }
        Value::Map(map) => write_json_map(out, map),
    }
}

pub(crate) fn write_json_map(out: &mut String, map: &Map) {
    out.push('{');
    for (index, (key, item)) in map.iter().enumerate() {
        if index > 0 {
            out.push(',');
        }
        write_json_string(out, key);
        out.push(':');
        write_json(out, item);
    }
    out.push('}');
}

/// The XML text of a scalar, as kit-py's `_text()` writes it.
pub(crate) fn xml_text(value: &Value) -> String {
    match value {
        Value::Bool(flag) => if *flag { "true" } else { "false" }.into(),
        Value::Int(number) => number.to_string(),
        // format(float, "f") with its trailing zeros and point removed.
        Value::Float(number) => {
            let fixed = format!("{number:.6}");
            fixed.trim_end_matches('0').trim_end_matches('.').to_owned()
        }
        // format(Decimal, "f"): exact, keeping its scale.
        Value::Decimal(number) => number.to_string(),
        Value::Money(text) | Value::Text(text) => text.clone(),
        Value::DateTime(moment) => datetime_text(moment),
        Value::Date(day) => date_text(day),
        Value::Null | Value::List(_) | Value::Map(_) => String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floats_print_as_python_repr() {
        for (value, expected) in [
            (0.0, "0.0"),
            (-0.0, "-0.0"),
            (1.5, "1.5"),
            (2.0, "2.0"),
            (-0.5, "-0.5"),
            (0.1, "0.1"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (1.5e-7, "1.5e-07"),
            (1e15, "1000000000000000.0"),
            (1e16, "1e+16"),
            (1.2345678901234567e19, "1.2345678901234567e+19"),
            (123456.789, "123456.789"),
            (f64::NAN, "NaN"),
            (f64::INFINITY, "Infinity"),
            (f64::NEG_INFINITY, "-Infinity"),
        ] {
            assert_eq!(python_float(value), expected, "{value:?}");
        }
    }

    #[test]
    fn strings_escape_as_json_dumps_does() {
        let mut out = String::new();
        write_json_string(&mut out, "a<b&c\"'\\\n\u{1}ñé€😀\u{7f}");
        assert_eq!(
            out,
            "\"a<b&c\\\"'\\\\\\n\\u0001\\u00f1\\u00e9\\u20ac\\ud83d\\ude00\\u007f\""
        );
    }

    #[test]
    fn money_is_two_places_half_even() {
        assert_eq!(money(Decimal::new(25, 1)), Value::Money("2.50".into()));
        assert_eq!(money(Decimal::new(125, 3)), Value::Money("0.12".into()));
        assert_eq!(money(Decimal::new(135, 3)), Value::Money("0.14".into()));
        assert_eq!(money(Decimal::from(7)), Value::Money("7.00".into()));
    }

    #[test]
    fn timestamps_are_utc_with_microseconds_only_when_present() {
        let at = Utc.with_ymd_and_hms(2026, 1, 2, 3, 4, 5).unwrap();
        assert_eq!(datetime_text(&at), "2026-01-02T03:04:05Z");
        let at = at.with_nanosecond(123_000_000).unwrap();
        assert_eq!(datetime_text(&at), "2026-01-02T03:04:05.123000Z");
    }

    #[test]
    fn xml_numbers_match_kit_py() {
        assert_eq!(xml_text(&Value::Float(2.5)), "2.5");
        assert_eq!(xml_text(&Value::Float(0.0)), "0");
        assert_eq!(xml_text(&Value::Float(1e20)), "100000000000000000000");
        assert_eq!(xml_text(&Value::Decimal(Decimal::new(150, 2))), "1.50");
    }
}
