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

/// ADR-0078: `public.product` and the four tables of the products boundary,
/// read and never written, and no `api_` object.
#[test]
fn it_reads_only_the_five_relations_adr_0078_allows() {
    let source = fs::read_to_string("src/lib.rs").expect("source");
    assert!(!source.contains("api_products"));
    let allowed = [
        "public.product",
        "products.product_status",
        "products.product_attribute",
        "products.product_barcode",
        "products.product_relation",
    ];
    let mut reads = 0;
    for line in source.lines() {
        for keyword in ["FROM ", "JOIN "] {
            let Some(start) = line.find(keyword) else {
                continue;
            };
            let name: String = line[start + keyword.len()..]
                .chars()
                .take_while(|c| c.is_ascii_alphanumeric() || *c == '_' || *c == '.')
                .collect();
            assert!(allowed.contains(&name.as_str()), "{line}");
            reads += 1;
        }
    }
    assert!(reads >= 8, "the statements were not found: {reads}");
    for statement in [
        "INSERT INTO",
        "UPDATE public",
        "UPDATE products",
        "DELETE FROM",
    ] {
        assert!(!source.contains(statement), "{statement}");
    }
}
