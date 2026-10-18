//! `mosaiq-kit` for Rust: the mechanisms every MOSAIQ Rust service shares,
//! with the behaviour of the Python kit (`packages/kit-py`), on axum.
//!
//! The crate holds mechanism only (exception E-21, ADR-0045): no business
//! rule, no role code, no Redis key but the session liveness prefix and the
//! stream prefix. A service pins it by tag (README.md).
//!
//! ```no_run
//! use mosaiq_kit::{Kit, Map, Representation, Reply, money};
//!
//! # async fn run() -> Result<(), Box<dyn std::error::Error>> {
//! let kit = Kit::builder("stores", "stores")
//!     .contracts_dir("contracts")
//!     .ready_check(|| async { true })
//!     .build()?;
//! let app = kit.routes().into_router();
//! let listener = tokio::net::TcpListener::bind("0.0.0.0:5005").await?;
//! axum::serve(listener, app).await?;
//! # Ok(()) }
//! ```

mod contract;
mod error;
mod kit;
pub mod logging;
mod render;
mod request;
mod value;

pub use contract::ContractError;
pub use error::{ApiError, Detail};
pub use kit::{Context, Kit, KitBuilder, Operation, ReadyCheck, Routes};
pub use logging::configure_logging;
pub use render::{COMMON_NAMESPACE, Collection, Page, Reply, Representation};
pub use request::{Format, MAX_BODY_BYTES};
pub use value::{Map, Value, money};
