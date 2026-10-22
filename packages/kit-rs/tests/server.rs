//! The listener's read timeouts, against a plain router.

use std::time::Duration;

use axum::extract::ConnectInfo;
use axum::routing::get;
use axum::Router;
use mosaiq_kit::serve_with_timeout;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

async fn start(timeout: Duration) -> std::net::SocketAddr {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let router =
        Router::new().route(
            "/",
            get(
                |ConnectInfo(peer): ConnectInfo<std::net::SocketAddr>| async move {
                    peer.ip().to_string()
                },
            ),
        );
    tokio::spawn(serve_with_timeout(listener, router, timeout));
    address
}

async fn closed_within(stream: &mut TcpStream, limit: Duration) -> bool {
    let mut buffer = [0u8; 512];
    tokio::time::timeout(limit, async {
        loop {
            match stream.read(&mut buffer).await {
                Ok(0) | Err(_) => return,
                Ok(_) => {}
            }
        }
    })
    .await
    .is_ok()
}

#[tokio::test]
async fn a_connection_that_sends_nothing_is_closed() {
    let address = start(Duration::from_millis(300)).await;
    let mut stream = TcpStream::connect(address).await.unwrap();
    assert!(closed_within(&mut stream, Duration::from_secs(3)).await);
}

#[tokio::test]
async fn a_stalled_request_head_is_closed() {
    let address = start(Duration::from_millis(300)).await;
    let mut stream = TcpStream::connect(address).await.unwrap();
    stream
        .write_all(b"GET / HTTP/1.1\r\nHost: x\r\n")
        .await
        .unwrap();
    assert!(closed_within(&mut stream, Duration::from_secs(3)).await);
}

#[tokio::test]
async fn a_prompt_request_is_answered_with_the_peer_address() {
    let address = start(Duration::from_secs(5)).await;
    let mut stream = TcpStream::connect(address).await.unwrap();
    stream
        .write_all(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        .await
        .unwrap();
    let mut answer = String::new();
    stream.read_to_string(&mut answer).await.unwrap();
    assert!(answer.starts_with("HTTP/1.1 200"), "{answer}");
    assert!(answer.ends_with("127.0.0.1"), "{answer}");
}
