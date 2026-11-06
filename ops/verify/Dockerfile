# The toolbox of the one-command verification run (F20-03, #761).
#
# One image holds every tool the suites need and its own Docker daemon, so the
# host needs only Docker: ops/verify/run.sh starts this image privileged and
# everything else (the stack, each throwaway database, every service image)
# runs on the daemon inside it, from empty, and goes away with it.
#
# It is linux/amd64 on every host, like the GitHub runners: the contract tools
# contracts/install-tools.sh installs are amd64 binaries, xmllint is pinned to
# Ubuntu 24.04's libxml, and the Android build tools are x86_64 only. On an
# Apple Silicon Mac it runs under emulation, slower but with the same tools.
#
# Every version is pinned here. What depends on the commit under test (the
# Python requirements, the Rust toolchains, the npm and Gradle dependencies) is
# installed by suites.sh from that commit's own lock files.

FROM docker:29.9.3-dind@sha256:d035bd30886e451b2727ef3cdb9af9a5c537a10d4bc5d18c08d0ffa597e46aeb AS docker

FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# The runner image's packages: Python 3.12, PostgreSQL 16's initdb for the
# scratch-cluster tests, libxml2 2.9.14, the Rust services' build libraries,
# JDK 21 for Gradle, and iptables for the inner Docker daemon. sudo is here
# because contracts/install-tools.sh calls it, as it would on a runner.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git openssl sudo xz-utils unzip \
      python3 python3-venv python3-pip python3-dev \
      postgresql-16 postgresql-client-16 \
      libxml2-utils libxml2-dev libclang-dev pkg-config build-essential \
      openjdk-21-jdk-headless \
      iptables kmod procps \
 && rm -rf /var/lib/apt/lists/*

# initdb refuses to run as root, so the database job's steps run as `runner`,
# the user a GitHub runner has.
RUN useradd --create-home --shell /bin/bash runner

# Docker 29.9.3's static binaries and the dind wrapper, from the official image.
COPY --from=docker /usr/local/bin/ /usr/local/bin/
COPY --from=docker /usr/local/libexec/docker/cli-plugins/ /usr/local/libexec/docker/cli-plugins/
RUN update-alternatives --set iptables /usr/sbin/iptables-legacy \
 && update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy

# Node 22.12.0, as desktop.yml, and mongosh 2.13.0, as services.yml.
ARG NODE_VERSION=22.12.0
ARG NODE_SHA256=22982235e1b71fa8850f82edd09cdae7e3f32df1764a9ec298c72d25ef2c164f
RUN curl -fsSLo /tmp/node.tar.xz "https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-linux-x64.tar.xz" \
 && echo "$NODE_SHA256  /tmp/node.tar.xz" | sha256sum --check --status \
 && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
 && rm /tmp/node.tar.xz \
 && npm install --global mongosh@2.13.0 >/dev/null

# rustup only; each crate's toolchain is installed from its own pin at run time.
ARG RUSTUP_VERSION=1.29.3
ARG RUSTUP_SHA256=b1c8cb14e30889e7e2999e6b69e62d9e9b9ffc0f4df8180564d306d9680e6c55
ENV RUSTUP_HOME=/opt/rustup CARGO_HOME=/opt/cargo PATH=/opt/cargo/bin:$PATH
RUN curl -fsSLo /tmp/rustup-init "https://static.rust-lang.org/rustup/archive/$RUSTUP_VERSION/x86_64-unknown-linux-gnu/rustup-init" \
 && echo "$RUSTUP_SHA256  /tmp/rustup-init" | sha256sum --check --status \
 && chmod +x /tmp/rustup-init \
 && /tmp/rustup-init -y --no-modify-path --profile minimal --default-toolchain none \
 && rm /tmp/rustup-init

# The Android command-line tools, with the SDK licenses accepted; Gradle
# downloads the platform and build tools the app's compileSdk names. On an
# empty SDK `--licenses` can write no file, so platform-tools is installed to
# write android-sdk-license, the license the platforms and build tools need,
# and the build fails if it is missing.
ARG ANDROID_CMDLINE=16111833
ARG ANDROID_CMDLINE_SHA1=e025545c62a8e64c7559119566a569fb1dec5f60
ENV ANDROID_HOME=/opt/android-sdk ANDROID_SDK_ROOT=/opt/android-sdk
RUN curl -fsSLo /tmp/cmdline.zip "https://dl.google.com/android/repository/commandlinetools-linux-${ANDROID_CMDLINE}_latest.zip" \
 && echo "$ANDROID_CMDLINE_SHA1  /tmp/cmdline.zip" | sha1sum --check --status \
 && mkdir -p "$ANDROID_HOME/cmdline-tools" \
 && unzip -q /tmp/cmdline.zip -d "$ANDROID_HOME/cmdline-tools" \
 && mv "$ANDROID_HOME/cmdline-tools/cmdline-tools" "$ANDROID_HOME/cmdline-tools/latest" \
 && rm /tmp/cmdline.zip \
 && yes | "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" --licenses >/dev/null \
 && yes | "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" --install platform-tools >/dev/null \
 && test -s "$ANDROID_HOME/licenses/android-sdk-license"

# uv 0.12.3, as ci.yml and services.yml, in the virtualenv the suites use.
ENV VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:/opt/cargo/bin:$PATH
RUN python3 -m venv /opt/venv && pip install --no-cache-dir uv==0.12.3

COPY entrypoint.sh suites.sh report.py /opt/verify/
WORKDIR /work
ENTRYPOINT ["bash", "/opt/verify/entrypoint.sh"]
