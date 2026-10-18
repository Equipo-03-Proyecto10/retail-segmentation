//! The errors a service may expose through the common error envelope.

use std::fmt;

/// One entry of the envelope's `details`: the offending field and a stable code.
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

/// An error that is safe to expose through the common error envelope
/// (`contracts/common/v1`), as `mosaiq_kit.ApiError` is in kit-py.
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

    pub(crate) fn payload_too_large() -> Self {
        Self::new(413, "payload_too_large", "The request body exceeds 64 KiB.")
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
