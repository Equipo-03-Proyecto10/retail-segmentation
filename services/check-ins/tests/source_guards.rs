//! The repository rules a reviewer would otherwise check by eye.

use std::fs;

#[test]
fn kit_is_pinned_and_service_has_no_workspace() {
    let manifest = fs::read_to_string("Cargo.toml").expect("Cargo.toml");
    assert!(manifest.contains("tag = \"kit-rs/v0.1.0\""));
    assert!(!manifest.contains("[workspace]"));
    assert!(!manifest.contains("path ="));
}

/// #479: `grep -rnE 'format!\(.*(SELECT|INSERT|UPDATE|DELETE)' src` prints
/// nothing. Every statement is fixed text with bound parameters.
#[test]
fn no_sql_is_built_with_format() {
    for entry in fs::read_dir("src").expect("src") {
        let path = entry.expect("entry").path();
        let source = fs::read_to_string(&path).expect("source");
        for line in source.lines().filter(|line| line.contains("format!(")) {
            for statement in ["SELECT", "INSERT", "UPDATE", "DELETE"] {
                assert!(
                    !line.contains(statement),
                    "{}: SQL must not be interpolated: {line}",
                    path.display()
                );
            }
        }
    }
}

#[test]
fn check_ins_are_never_updated_or_deleted() {
    let source = fs::read_to_string("src/lib.rs").expect("service source");
    assert!(!source.contains("UPDATE checkins."));
    assert!(!source.contains("DELETE FROM checkins."));
    // The location comes from the stores contract view and nowhere else.
    assert!(source.contains("FROM api_stores.store_directory_v1"));
    assert!(!source.contains("stores.store_location"));
}
