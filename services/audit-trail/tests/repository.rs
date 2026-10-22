//! The repository rules a reviewer would otherwise check by eye (#483), and
//! redis/audit-trail.acl against a real Redis when TEST_REDIS_URL names one.

use std::fs;

const ACL: &str = "user svc_audit_trail on {{password}} resetchannels -@all +@connection \
                   +@read +@write -@dangerous -scan -randomkey ~audit_trail:rl:* \
                   (%R~pub:auth:v1:live:* +exists)";

#[test]
fn kit_is_pinned_and_service_has_no_workspace() {
    let manifest = fs::read_to_string("Cargo.toml").expect("Cargo.toml");
    assert!(manifest.contains("tag = \"kit-rs/v0.1.4\""));
    assert!(!manifest.contains("[workspace]"));
    assert!(!manifest.contains("path ="));
    assert!(fs::metadata("Cargo.lock").is_ok());
    let version = fs::read_to_string("VERSION").expect("VERSION");
    assert_eq!(version.trim().split('.').count(), 3);
}

/// #483: `grep -rnE 'format!\(.*(SELECT|INSERT|UPDATE|DELETE)' src` prints
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
fn the_service_only_reads_and_only_the_one_table() {
    let source = fs::read_to_string("src/lib.rs").expect("service source");
    for write in [
        "INSERT INTO",
        "UPDATE public",
        "DELETE FROM",
        "TRUNCATE",
        ".transaction(",
    ] {
        assert!(!source.contains(write), "{write}");
    }
    let tables: Vec<&str> = source
        .split("FROM public.")
        .skip(1)
        .map(|rest| {
            rest.split(|c: char| !c.is_alphanumeric() && c != '_')
                .next()
                .unwrap()
        })
        .collect();
    assert!(!tables.is_empty() && tables.iter().all(|table| *table == "audit_log"));
    assert!(!source.contains("XREADGROUP") && !source.contains("OutboxRelay"));
}

#[test]
fn the_acl_file_is_exactly_the_one_audit_trail_user_line() {
    let line = fs::read_to_string("redis/audit-trail.acl").expect("ACL");
    assert_eq!(line.lines().collect::<Vec<_>>(), [ACL]);
}

#[tokio::test]
async fn the_acl_denies_other_keys_and_streams_in_a_real_redis() {
    let Ok(url) = std::env::var("TEST_REDIS_URL") else {
        if std::env::var("MOSAIQ_REQUIRE_REDIS").as_deref() == Ok("1") {
            panic!("TEST_REDIS_URL is missing, and MOSAIQ_REQUIRE_REDIS forbids skipping");
        }
        eprintln!("skipped: TEST_REDIS_URL is not set");
        return;
    };
    let mut admin = redis::Client::open(url.as_str())
        .unwrap()
        .get_multiplexed_async_connection()
        .await
        .unwrap();
    let user = "svc_audit_trail_acltest";
    let _: i64 = redis::cmd("ACL")
        .arg("DELUSER")
        .arg(user)
        .query_async(&mut admin)
        .await
        .unwrap();
    let rules = ACL.replace("{{password}}", ">pw");
    let mut setuser = redis::cmd("ACL");
    setuser.arg("SETUSER").arg(user);
    let arguments: Vec<&str> = rules.split(' ').collect();
    let mut index = 3; // after `user svc_audit_trail`
    while index < arguments.len() {
        if arguments[index].starts_with('(') {
            setuser.arg(arguments[index..].join(" "));
            break;
        }
        setuser.arg(arguments[index]);
        index += 1;
    }
    let _: String = setuser.query_async(&mut admin).await.unwrap();

    let dryrun = |command: &[&str]| {
        let mut call = redis::cmd("ACL");
        call.arg("DRYRUN").arg(user);
        for part in command {
            call.arg(*part);
        }
        call
    };
    for allowed in [
        vec!["PING"],
        vec!["EXISTS", "pub:auth:v1:live:session"],
        vec!["INCR", "audit_trail:rl:actor:window"],
    ] {
        let answer: String = dryrun(&allowed).query_async(&mut admin).await.unwrap();
        assert_eq!(answer, "OK", "{allowed:?}");
    }
    for denied in [
        vec!["GET", "auth:session:x"],
        vec!["GET", "pub:auth:v1:live:session"],
        vec![
            "XREADGROUP",
            "GROUP",
            "audit_trail",
            "c",
            "STREAMS",
            "stream:users.v1.access_changed",
            ">",
        ],
        vec!["XADD", "stream:users.v1.access_changed", "*", "a", "b"],
        vec!["SCAN", "0"],
        vec!["INCR", "stores:rl:actor:window"],
    ] {
        let answer: String = dryrun(&denied).query_async(&mut admin).await.unwrap();
        assert_ne!(answer, "OK", "{denied:?}");
    }
    let _: i64 = redis::cmd("ACL")
        .arg("DELUSER")
        .arg(user)
        .query_async(&mut admin)
        .await
        .unwrap();
}
