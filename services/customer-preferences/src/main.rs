use mosaiq_kit::{configure_logging, KitConfig};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    configure_logging(&std::env::var("LOG_LEVEL").unwrap_or_else(|_| "info".to_owned()));
    // ADR-0040's budget for a Rust service: two tokio workers at 0.25 CPU.
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()?;
    runtime.block_on(customer_preferences::serve(
        KitConfig::from_env(customer_preferences::SUFFIX),
        "contracts",
    ))
}
