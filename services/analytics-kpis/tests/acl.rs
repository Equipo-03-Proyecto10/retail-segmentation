//! The committed ACL line, loaded into a real Redis (TEST_REDIS_URL, the admin
//! user) under a throwaway password and checked with ACL DRYRUN.

use redis::aio::MultiplexedConnection;
use uuid::Uuid;

const ACL: &str = "redis/analytics-kpis.acl";
const USER: &str = "svc_kpis";

/// Loads and deletes the same ACL user, so the Redis tests take turns.
static REDIS_USER: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

fn redis_url() -> Option<String> {
    match std::env::var("TEST_REDIS_URL") {
        Ok(url) if !url.is_empty() => Some(url),
        _ => {
            if std::env::var("MOSAIQ_REQUIRE_REDIS").as_deref() == Ok("1") {
                panic!("TEST_REDIS_URL is missing, and MOSAIQ_REQUIRE_REDIS forbids skipping");
            }
            eprintln!("skipped: TEST_REDIS_URL is not set");
            None
        }
    }
}

async fn admin(url: &str) -> MultiplexedConnection {
    redis::Client::open(url)
        .unwrap()
        .get_multiplexed_async_connection()
        .await
        .unwrap()
}

/// Load the committed user line under a throwaway password.
async fn load_acl(admin: &mut MultiplexedConnection) {
    let password = Uuid::new_v4().simple().to_string();
    let line = std::fs::read_to_string(ACL).unwrap();
    let tokens = acl_tokens(line.trim());
    assert_eq!(&tokens[..3], ["user", USER, "on"]);
    delete_user(admin).await;
    let mut setuser = redis::cmd("ACL");
    setuser.arg("SETUSER").arg(USER).arg("on");
    for token in &tokens[3..] {
        if token == "{{password}}" {
            setuser.arg(format!(">{password}"));
        } else {
            setuser.arg(token);
        }
    }
    let _: () = setuser.query_async(admin).await.unwrap();
}

async fn delete_user(admin: &mut MultiplexedConnection) {
    let _: () = redis::cmd("ACL")
        .arg("DELUSER")
        .arg(USER)
        .query_async(admin)
        .await
        .unwrap();
}

async fn dry_run(admin: &mut MultiplexedConnection, command: &[&str]) -> String {
    let mut dry = redis::cmd("ACL");
    dry.arg("DRYRUN").arg(USER);
    for part in command {
        dry.arg(*part);
    }
    dry.query_async(admin).await.unwrap()
}

#[tokio::test]
async fn the_acl_allows_its_own_keys_and_denies_the_rest() {
    let Some(url) = redis_url() else { return };
    let _turn = REDIS_USER.lock().await;
    let mut admin = admin(&url).await;
    load_acl(&mut admin).await;
    for allowed in [
        vec!["EXISTS", "pub:auth:v1:live:x"],
        vec!["INCR", "kpis:rl:x"],
        vec!["EXPIRE", "kpis:rl:x", "60"],
    ] {
        assert_eq!(dry_run(&mut admin, &allowed).await, "OK", "{allowed:?}");
    }
    for denied in [
        // It publishes and consumes no stream (RN-68).
        vec!["XADD", "stream:sales.v1.recorded", "*", "a", "b"],
        vec!["XADD", "stream:kpis.v1.anything", "*", "a", "b"],
        vec![
            "XREADGROUP",
            "GROUP",
            "g",
            "c",
            "STREAMS",
            "stream:sales.v1.recorded",
            ">",
        ],
        vec!["GET", "auth:session:x"],
        vec!["GET", "segments:rl:x"],
        vec!["GET", "pub:auth:v1:live:x"],
        vec!["SET", "pub:auth:v1:live:x", "1"],
        vec!["SCAN", "0"],
        vec!["KEYS", "*"],
        vec!["RANDOMKEY"],
        vec!["FLUSHALL"],
    ] {
        assert_ne!(dry_run(&mut admin, &denied).await, "OK", "{denied:?}");
    }
    delete_user(&mut admin).await;
}

/// ACL tokens, keeping a parenthesised selector as one token.
fn acl_tokens(line: &str) -> Vec<String> {
    let mut tokens = Vec::new();
    let mut current = String::new();
    let mut depth = 0;
    for character in line.chars() {
        match character {
            '(' => {
                depth += 1;
                current.push(character);
            }
            ')' => {
                depth -= 1;
                current.push(character);
            }
            ' ' if depth == 0 => {
                if !current.is_empty() {
                    tokens.push(std::mem::take(&mut current));
                }
            }
            other => current.push(other),
        }
    }
    if !current.is_empty() {
        tokens.push(current);
    }
    tokens
}

#[test]
fn the_acl_is_one_user_line_with_the_documented_selectors() {
    let line = std::fs::read_to_string(ACL).unwrap();
    assert_eq!(line.trim().lines().count(), 1);
    assert!(line.starts_with("user svc_kpis on {{password}} "));
    for part in [
        "-@all +@connection +@read +@write -@dangerous -scan -randomkey",
        "~kpis:rl:*",
        "(%R~pub:auth:v1:live:* +exists)",
    ] {
        assert!(line.contains(part), "{part}");
    }
    // No stream, no cache and no WAITAOF: it keeps no state of its own.
    for absent in ["stream:", "cache", "+waitaof", "+xadd"] {
        assert!(!line.contains(absent), "{absent}");
    }
    for token in line.split_whitespace() {
        let granted = token.trim_matches(|c| c == '(' || c == ')');
        assert!(
            granted != "+scan" && granted != "+keys" && granted != "+@all",
            "{token}"
        );
    }
}
