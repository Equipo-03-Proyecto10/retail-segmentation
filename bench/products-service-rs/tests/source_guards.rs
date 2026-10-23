//! ADR-0042's rules for the twin, checked in its own tests.

use std::fs;

fn sources() -> Vec<(String, String)> {
    fs::read_dir("src")
        .expect("src")
        .map(|entry| {
            let path = entry.expect("entry").path();
            (
                path.display().to_string(),
                fs::read_to_string(&path).expect("source"),
            )
        })
        .collect()
}

#[test]
fn kit_is_pinned_by_tag_and_there_is_no_workspace() {
    let manifest = fs::read_to_string("Cargo.toml").expect("Cargo.toml");
    assert!(manifest.contains("tag = \"kit-rs/v0.1.0\""));
    assert!(!manifest.contains("[workspace]"));
    assert!(!manifest.contains("path ="));
}

/// `grep -rnE '\.(post|put|patch|delete)\(' bench/products-service-rs/src/`.
#[test]
fn the_twin_registers_no_write() {
    for (path, source) in sources() {
        for method in [".post(", ".put(", ".patch(", ".delete("] {
            assert!(!source.contains(method), "{path} calls {method}");
        }
    }
}

#[test]
fn no_sql_is_built_with_format() {
    for (path, source) in sources() {
        for line in source.lines().filter(|line| line.contains("format!(")) {
            for statement in ["SELECT", "INSERT", "UPDATE", "DELETE"] {
                assert!(!line.contains(statement), "{path}: {line}");
            }
        }
    }
}

#[test]
fn it_reads_only_public_product() {
    let source = fs::read_to_string("src/lib.rs").expect("source");
    assert_eq!(source.matches("FROM public.product").count(), 4);
    assert!(!source.contains("api_products"));
}
