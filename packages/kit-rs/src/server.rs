//! The HTTP listener every Rust service runs, with read timeouts.
//!
//! `axum::serve` sets none, so a client that connects and sends nothing, or
//! stalls in the middle of a request head, holds its connection and task open
//! for ever. Here a connection must deliver each request head within
//! [`HEADER_READ_TIMEOUT`]. The same timer ends a kept-alive connection that
//! stays idle that long, so one timeout covers both half-open and idle clients.

use std::time::Duration;

use axum::extract::{ConnectInfo, Request};
use axum::Router;
use hyper::body::Incoming;
use hyper_util::rt::{TokioIo, TokioTimer};
use hyper_util::service::TowerToHyperService;
use tokio::net::TcpListener;
use tower::ServiceExt;

/// Time allowed for a request head to arrive, and for an idle connection.
pub const HEADER_READ_TIMEOUT: Duration = Duration::from_secs(10);

/// Serve `router` on `listener` until the process ends.
pub async fn serve(listener: TcpListener, router: Router) -> std::io::Result<()> {
    serve_with_timeout(listener, router, HEADER_READ_TIMEOUT).await
}

/// [`serve`] with an explicit head and idle timeout.
pub async fn serve_with_timeout(
    listener: TcpListener,
    router: Router,
    timeout: Duration,
) -> std::io::Result<()> {
    loop {
        let (stream, address) = match listener.accept().await {
            Ok(accepted) => accepted,
            Err(error) => {
                // Descriptor exhaustion and the like clear up; do not spin.
                tracing::warn!("Accepting a connection failed: {error}");
                tokio::time::sleep(Duration::from_secs(1)).await;
                continue;
            }
        };
        let router = router.clone();
        tokio::spawn(async move {
            let service = TowerToHyperService::new(tower::service_fn(
                move |mut request: hyper::Request<Incoming>| {
                    request.extensions_mut().insert(ConnectInfo(address));
                    let request: Request = request.map(axum::body::Body::new);
                    router.clone().oneshot(request)
                },
            ));
            let connection = hyper::server::conn::http1::Builder::new()
                .timer(TokioTimer::new())
                .header_read_timeout(timeout)
                .serve_connection(TokioIo::new(stream), service);
            if let Err(error) = connection.await {
                tracing::debug!("Connection from {address} ended: {error}");
            }
        });
    }
}
