# A Rust service's image (ADR-0040), rendered by render.py into
# services/stock/Dockerfile. The build context is services/stock/ and
# nothing above it (ADR-0045): CI copies contracts/stock/ and
# contracts/common/v1/ into services/stock/contracts/ (git-ignored) before
# `docker build`, and this file copies them from inside that context.
#
# Layout it expects: its own Cargo.toml (package and binary named stock, no
# [workspace]) and committed Cargo.lock, src/, and contracts/.

FROM rust:1.98.1-slim-trixie@sha256:4cd829461bd5c4d511c32e269da9cb8929223b666519d8004e35fc8d1d771ab7 AS build
RUN apt-get update \
    && apt-get install -y --no-install-recommends libclang-dev libxml2-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY src/ ./src/
RUN cargo build --release --locked \
    && install -m 0755 target/release/stock /usr/local/bin/stock

FROM debian:trixie-20260918-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a
RUN apt-get update \
    && apt-get install -y --no-install-recommends libxml2 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /usr/local/bin/stock /usr/local/bin/stock
COPY contracts/ ./contracts/

USER 10001:10001
EXPOSE 5006
ENTRYPOINT ["/usr/local/bin/stock"]
