//! Repository rules this service must keep (ADR-0034, ADR-0040, ADR-0045, AGENTS.md).

use std::fs;
use std::path::Path;

fn rust_files(dir: &Path, found: &mut Vec<std::path::PathBuf>) {
    for entry in fs::read_dir(dir).unwrap() {
        let path = entry.unwrap().path();
        if path.is_dir() {
            rust_files(&path, found);
        } else if path.extension().is_some_and(|ext| ext == "rs") {
            found.push(path);
        }
    }
}

#[test]
fn kit_is_pinned_by_tag_and_the_crate_has_no_workspace_or_path_dependency() {
    let manifest = fs::read_to_string("Cargo.toml").unwrap();
    assert!(manifest.contains("tag = \"kit-rs/v0.1.5\""));
    assert!(!manifest.contains("[workspace]"));
    assert!(!manifest.contains("path ="));
}

#[test]
fn the_lock_file_and_the_issues_files_exist() {
    for file in [
        "Cargo.toml",
        "Cargo.lock",
        "Dockerfile",
        "VERSION",
        "compose.yaml",
        "nginx/segments.conf",
        "redis/segments.acl",
        "src",
        "tests",
    ] {
        assert!(Path::new(file).exists(), "{file}");
    }
    let version = fs::read_to_string("VERSION").unwrap();
    assert_eq!(version.trim().split('.').count(), 3, "semver VERSION");
}

#[test]
fn no_sql_is_built_by_format() {
    // The issue's guard: grep -rnE 'format!\(.*(SELECT|INSERT|UPDATE|DELETE)' src/
    let mut files = Vec::new();
    rust_files(Path::new("src"), &mut files);
    assert!(!files.is_empty(), "src/ holds no Rust file");
    for path in files {
        let source = fs::read_to_string(&path).unwrap();
        for line in source.lines() {
            if let Some(at) = line.find("format!(") {
                let rest = &line[at..];
                for keyword in ["SELECT", "INSERT", "UPDATE", "DELETE"] {
                    assert!(
                        !rest.contains(keyword),
                        "{}: SQL built by format!: {line}",
                        path.display()
                    );
                }
            }
        }
    }
}

#[test]
fn the_image_builds_from_its_own_directory_only() {
    let dockerfile = fs::read_to_string("Dockerfile").unwrap();
    for line in dockerfile.lines() {
        let line = line.trim_start();
        if line.starts_with("COPY") || line.starts_with("ADD") {
            assert!(!line.contains("../") && !line.contains("web/"), "{line}");
        }
    }
}

#[test]
fn the_compose_project_is_alone_and_hardened() {
    let compose = fs::read_to_string("compose.yaml").unwrap();
    for wanted in [
        "name: mosaiq-segments",
        "image: ghcr.io/equipo-03-proyecto10/mosaiq-segments:${IMAGE_TAG}",
        "\"127.0.0.1:5010:5010\"",
        "mem_limit: 64m",
        "cpus: 0.25",
        "user: \"10001:10001\"",
        "cap_drop: [ALL]",
        "no-new-privileges:true",
        "read_only: true",
        "networks: [mosaiq]",
        "external: true",
        "/segments.env",
    ] {
        assert!(compose.contains(wanted), "{wanted}");
    }
    assert!(!compose
        .lines()
        .any(|line| line.trim_start().starts_with("depends_on:")));
    // The socket URL for svc_segments lives in the host env file, whose local
    // stand-in is docker/distributed/svc/segments.env.
    let env = fs::read_to_string("../../docker/distributed/svc/segments.env").unwrap();
    assert!(env.contains("DATABASE_URL=postgresql://svc_segments@/retail?host=/run/postgresql"));
}

#[test]
fn nginx_forwards_the_real_client_and_caps_the_body() {
    let nginx = fs::read_to_string("nginx/segments.conf").unwrap();
    for wanted in [
        "location /api/segments/ {",
        "proxy_pass http://127.0.0.1:5010/;",
        "proxy_set_header X-Real-IP $remote_addr;",
        "proxy_set_header X-Forwarded-For $remote_addr;",
        "client_max_body_size 64k;",
    ] {
        assert!(nginx.contains(wanted), "{wanted}");
    }
}
