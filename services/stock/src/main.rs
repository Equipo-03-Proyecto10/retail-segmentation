use mosaiq_kit::{configure_logging, KitConfig};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    configure_logging(&std::env::var("LOG_LEVEL").unwrap_or_else(|_| "info".to_owned()));
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()?;
    runtime.block_on(stock::serve(KitConfig::from_env("stock"), "contracts"))
}
