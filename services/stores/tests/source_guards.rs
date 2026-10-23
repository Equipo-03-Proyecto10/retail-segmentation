use std::fs;

#[test]
fn kit_is_pinned_and_service_has_no_workspace() {
    let manifest = fs::read_to_string("Cargo.toml").expect("Cargo.toml");
    assert!(manifest.contains("tag = \"kit-rs/v0.1.5\""));
    assert!(!manifest.contains("[workspace]"));
    assert!(!manifest.contains("path ="));
}

#[test]
fn sql_uses_bound_parameters() {
    let source = fs::read_to_string("src/lib.rs").expect("service source");
    for statement in ["SELECT", "INSERT", "UPDATE", "DELETE"] {
        assert!(
            !source.contains(&format!("format!(\"{statement}")),
            "SQL must not be interpolated"
        );
    }
    assert!(source.contains("WHERE store_id = $1"));
}
