//! Repository rules this service must keep (ADR-0040, ADR-0045, AGENTS.md).

use std::fs;

#[test]
fn kit_is_pinned_by_tag_and_the_crate_has_no_workspace_or_path_dependency() {
    let manifest = fs::read_to_string("Cargo.toml").unwrap();
    assert!(manifest.contains("tag = \"kit-rs/v0.1.5\""));
    assert!(!manifest.contains("[workspace]"));
    assert!(!manifest.contains("path ="));
}

#[test]
fn no_sql_is_built_by_format() {
    // The issue's guard: grep -rnE 'format!\(.*(SELECT|INSERT|UPDATE|DELETE)' src/
    for entry in fs::read_dir("src").unwrap() {
        let path = entry.unwrap().path();
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
    let compose = fs::read_to_string("compose.yaml").unwrap();
    for wanted in [
        "name: mosaiq-customer-preferences",
        "image: ghcr.io/equipo-03-proyecto10/mosaiq-customer-preferences:${IMAGE_TAG}",
        "\"127.0.0.1:5008:5008\"",
        "mem_limit: 48m",
        "cpus: 0.25",
        "external: true",
    ] {
        assert!(compose.contains(wanted), "{wanted}");
    }
    assert!(!compose
        .lines()
        .any(|line| line.trim_start().starts_with("depends_on:")));
    let nginx = fs::read_to_string("nginx/customer-preferences.conf").unwrap();
    for wanted in [
        "location /api/customer-preferences/ {",
        "proxy_pass http://127.0.0.1:5008/;",
        "proxy_set_header X-Real-IP $remote_addr;",
        "proxy_set_header X-Forwarded-For $remote_addr;",
        "client_max_body_size 64k;",
    ] {
        assert!(nginx.contains(wanted), "{wanted}");
    }
}
