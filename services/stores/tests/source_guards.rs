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
    for path in ["src/lib.rs", "src/rules.rs"] {
        let source = fs::read_to_string(path).expect("service source");
        for statement in ["SELECT", "INSERT", "UPDATE", "DELETE"] {
            assert!(
                !source.contains(&format!("format!(\"{statement}")),
                "{path}: SQL must not be interpolated"
            );
        }
        assert!(source.contains("WHERE store_id = $1") || source.contains("s.store_id = $1"));
    }
}

#[test]
fn the_service_never_writes_the_directory_flag_the_web_owns() {
    let source = fs::read_to_string("src/rules.rs").expect("service source");
    assert!(!source.contains("public.store SET"));
    assert!(!source.contains("is_active ="));
}
