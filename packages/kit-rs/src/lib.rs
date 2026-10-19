//! Shared transport and infrastructure mechanisms for MOSAIQ Rust services.
//!
//! The crate verifies tokens, checks session liveness, drives contract-based
//! routes, negotiates JSON and XML, renders common errors, manages pagination,
//! health, rate limits, database attribution, outbox relays and stream
//! consumers. It holds no business rule and no role vocabulary (ADR-0045, E-21).

pub mod auth;
pub mod contract;
pub mod database;
pub mod kit;
pub mod logging;
pub mod rendering;
pub mod streams;
pub mod types;

pub use auth::{service_redis_info, Principal};
pub use contract::{Contract, KitError, Operation, PathValue};
pub use database::{Connection, Database};
pub use kit::{Ctx, Kit, KitConfig};
pub use logging::configure_logging;
pub use rendering::Format;
pub use streams::{OutboxRelay, StreamConsumer, StreamStore};
pub use types::{
    money, ApiError, Collection, Detail, NoContent, Page, Reply, Representation, Value,
};
