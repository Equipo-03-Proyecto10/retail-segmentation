# Build context: packages/kit-rs/. CI stages contracts/common/v1 into
# examples/contracts/common/ first, as for kit-py's example. The libxml crate
# generates its bindings with bindgen, hence libclang.
FROM rust:1.90.0-slim-trixie@sha256:7fa728f3678acf5980d5db70960cf8491aff9411976789086676bdf0c19db39e AS build

RUN apt-get update \
    && apt-get install -y --no-install-recommends libclang-dev libxml2-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY Cargo.toml Cargo.lock README.md ./
COPY src/ ./src/
COPY examples/kit-example.rs ./examples/kit-example.rs
RUN cargo build --release --locked --example kit-example

FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f

RUN apt-get update \
    && apt-get install -y --no-install-recommends libxml2 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /build/target/release/examples/kit-example /app/kit-example
COPY examples/contracts/ ./contracts/

USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["/app/kit-example"]
