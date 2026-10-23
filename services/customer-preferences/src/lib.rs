//! customer-preferences-service (F14-16): a customer's preferred channels and
//! interest categories are each replaced only atomically, as a whole set of
//! existing references (RN-54, ADR-0034).
//!
//! Each `PUT` runs in one transaction: the customer is checked through
//! `api_customers.customer_ref_v1`, the per-customer advisory lock
//! `pg_advisory_xact_lock(290001, hashtext(customer_id::text))` serializes
//! replaces of one customer, and the set is brought to the submitted list by a
//! DELETE of the ids it no longer holds and an INSERT of the ids it lacks.
//! Channels and categories are checked by foreign key, so 23503 maps to 422
//! by constraint name. Every statement is a constant with bound parameters.

use std::collections::HashSet;
use std::sync::Arc;
use std::time::Duration;

use axum::Router;
use mosaiq_kit::{
    service_redis_info, ApiError, Ctx, Database, Detail, Kit, KitConfig, OutboxRelay, Principal,
    Reply, Representation, Value,
};
use serde_json::Value as JsonValue;
use uuid::Uuid;

pub const SERVICE: &str = "customer-preferences";
/// The `svc_` role suffix: `svc_preferences`, `preferences_events`,
/// `preferences:rl:*` and `REDIS_PREFERENCES_PASSWORD`.
pub const SUFFIX: &str = "preferences";
pub const STREAM: &str = "stream:preferences.v1.changed";
pub const PORT: u16 = 5008;
/// The first key of the two-key advisory lock (contracts/db/advisory-locks.md).
pub const LOCK_CLASS: i32 = 290_001;

const CUSTOMER_EXISTS: &str =
    "SELECT customer_id FROM api_customers.customer_ref_v1 WHERE customer_id = $1";
const LOCK_CUSTOMER: &str = "SELECT pg_advisory_xact_lock($1, hashtext($2::uuid::text))";
/// One statement, so one snapshot: a reader never sees half of a replace.
const READ_PROFILE: &str = "SELECT \
     ARRAY(SELECT c.channel_id FROM public.customer_preferred_channel AS c \
           WHERE c.customer_id = r.customer_id ORDER BY c.channel_id), \
     ARRAY(SELECT i.category_id FROM public.customer_interest_category AS i \
           WHERE i.customer_id = r.customer_id ORDER BY i.category_id) \
     FROM api_customers.customer_ref_v1 AS r WHERE r.customer_id = $1";
/// The profile as the replacing transaction leaves it, with the audit actor
/// that transaction carries (ADR-0034).
const READ_REPLACED: &str = "SELECT \
     ARRAY(SELECT channel_id FROM public.customer_preferred_channel \
           WHERE customer_id = $1 ORDER BY channel_id), \
     ARRAY(SELECT category_id FROM public.customer_interest_category \
           WHERE customer_id = $1 ORDER BY category_id), \
     current_setting('mosaiq.user_id', true)";
/// Schema readiness: every object the service reads or writes, planned under
/// the service role and never executed.
const READY: &str = "SELECT FROM api_customers.customer_ref_v1, \
     public.customer_preferred_channel, public.customer_interest_category, \
     api_preferences.interest_category_v1, preferences_events.outbox LIMIT 0";

/// One of the two independent multivalued facts of a customer (4NF).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PreferenceSet {
    PreferredChannels,
    InterestCategories,
}

struct Statements {
    current: &'static str,
    remove: &'static str,
    insert: &'static str,
    reference_key: &'static str,
    customer_key: &'static str,
    unknown_code: &'static str,
    unknown_message: &'static str,
}

const CHANNELS: Statements = Statements {
    current: "SELECT channel_id FROM public.customer_preferred_channel WHERE customer_id = $1",
    remove: "DELETE FROM public.customer_preferred_channel \
             WHERE customer_id = $1 AND channel_id <> ALL($2::smallint[])",
    insert: "INSERT INTO public.customer_preferred_channel (customer_id, channel_id) \
             VALUES ($1, $2)",
    reference_key: "customer_preferred_channel_channel_id_fkey",
    customer_key: "customer_preferred_channel_customer_id_fkey",
    unknown_code: "unknown_channel",
    unknown_message: "A channel id references no channel.",
};

const CATEGORIES: Statements = Statements {
    current: "SELECT category_id FROM public.customer_interest_category WHERE customer_id = $1",
    remove: "DELETE FROM public.customer_interest_category \
             WHERE customer_id = $1 AND category_id <> ALL($2::smallint[])",
    insert: "INSERT INTO public.customer_interest_category (customer_id, category_id) \
             VALUES ($1, $2)",
    reference_key: "customer_interest_category_category_id_fkey",
    customer_key: "customer_interest_category_customer_id_fkey",
    unknown_code: "unknown_category",
    unknown_message: "A category id references no category.",
};

impl PreferenceSet {
    /// The request member that carries the set.
    pub fn field(self) -> &'static str {
        match self {
            Self::PreferredChannels => "channel_ids",
            Self::InterestCategories => "category_ids",
        }
    }

    fn statements(self) -> &'static Statements {
        match self {
            Self::PreferredChannels => &CHANNELS,
            Self::InterestCategories => &CATEGORIES,
        }
    }

    /// 422 naming the submitted position of the id that references nothing.
    fn unknown(self, index: usize) -> ApiError {
        let statements = self.statements();
        ApiError::new(422, statements.unknown_code, statements.unknown_message).with_details(vec![
            Detail::new(format!("{}[{index}]", self.field()), "unknown"),
        ])
    }

    /// Map an INSERT failure by constraint name; anything else stays the
    /// kit's 500 or 503.
    fn insert_error(self, error: tokio_postgres::Error, index: usize) -> ApiError {
        let statements = self.statements();
        let foreign_key = error
            .as_db_error()
            .filter(|database| database.code().code() == "23503")
            .and_then(|database| database.constraint());
        match foreign_key {
            Some(name) if name == statements.reference_key => self.unknown(index),
            Some(name) if name == statements.customer_key => customer_not_found(),
            _ => ApiError::from(error),
        }
    }
}

/// A customer's two sets, each sorted ascending.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Profile {
    pub customer_id: Uuid,
    pub preferred_channel_ids: Vec<i16>,
    pub interest_category_ids: Vec<i16>,
}

impl Profile {
    pub fn representation(&self) -> Representation {
        let list = |ids: &[i16]| Value::List(ids.iter().copied().map(Value::from).collect());
        Representation::new(
            "PreferenceProfile",
            [
                ("customer_id", Value::from(self.customer_id.to_string())),
                ("preferred_channel_ids", list(&self.preferred_channel_ids)),
                ("interest_category_ids", list(&self.interest_category_ids)),
            ],
        )
    }
}

/// A committed replace: the profile it left and the audit actor its
/// transaction carried.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Replaced {
    pub profile: Profile,
    pub audit_actor: Option<String>,
}

pub fn customer_not_found() -> ApiError {
    ApiError::new(404, "customer_not_found", "The customer does not exist.")
}

fn invalid_body(field: &str, code: &str, message: &str) -> ApiError {
    ApiError::new(422, "invalid_body", message).with_details(vec![Detail::new(field, code)])
}

/// The integer an item names, as decimal text, so `5` and `5.0` are one id.
fn integer_text(item: &JsonValue) -> Option<String> {
    let number = item.as_number()?;
    number
        .as_i64()
        .map(|value| value.to_string())
        .or_else(|| number.as_u64().map(|value| value.to_string()))
        .or_else(|| {
            number
                .as_f64()
                .filter(|value| value.is_finite() && value.fract() == 0.0)
                .map(|value| format!("{value:.0}"))
        })
}

/// The submitted ids in order, refusing a repeated id with 422
/// `invalid_body`. `None` marks an id outside the smallint key range, which
/// can name no channel or category.
pub fn submitted_ids(set: PreferenceSet, body: &JsonValue) -> Result<Vec<Option<i16>>, ApiError> {
    let field = set.field();
    let items = body
        .get(field)
        .and_then(JsonValue::as_array)
        .ok_or_else(|| invalid_body(field, "required", "The list of ids is required."))?;
    let mut seen = HashSet::with_capacity(items.len());
    let mut ids = Vec::with_capacity(items.len());
    for item in items {
        let text = integer_text(item)
            .ok_or_else(|| invalid_body(field, "invalid_type", "Each id is an integer."))?;
        let id = text.parse::<i16>().ok();
        if !seen.insert(text) {
            return Err(invalid_body(
                field,
                "duplicate",
                "The list contains the same id twice.",
            ));
        }
        ids.push(id);
    }
    Ok(ids)
}

fn profile_from(customer_id: Uuid, row: &tokio_postgres::Row) -> Result<Profile, ApiError> {
    Ok(Profile {
        customer_id,
        preferred_channel_ids: row.try_get(0)?,
        interest_category_ids: row.try_get(1)?,
    })
}

/// Read a profile in one statement; `None` when the customer does not exist.
pub async fn read_profile(db: &Database, customer_id: Uuid) -> Result<Option<Profile>, ApiError> {
    let connection = db.connection().await?;
    let row = connection.query_opt(READ_PROFILE, &[&customer_id]).await?;
    row.map(|row| profile_from(customer_id, &row)).transpose()
}

/// Replace one whole set as `actor`, all or nothing (RN-54).
///
/// `ids` is the submitted list in order, already free of repeats. The first
/// position that references nothing is the one the 422 names, and the
/// transaction then rolls back, so the previous set is unchanged. A replace
/// that changes nothing writes nothing, so it captures no event.
pub async fn replace(
    db: &Database,
    actor: &Principal,
    customer_id: Uuid,
    set: PreferenceSet,
    ids: &[Option<i16>],
) -> Result<Replaced, ApiError> {
    let statements = set.statements();
    let mut connection = db.connection().await?;
    let transaction = connection.transaction(Some(actor)).await?;
    if transaction
        .query_opt(CUSTOMER_EXISTS, &[&customer_id])
        .await?
        .is_none()
    {
        return Err(customer_not_found());
    }
    transaction
        .execute(LOCK_CUSTOMER, &[&LOCK_CLASS, &customer_id])
        .await?;

    let current = transaction
        .query(statements.current, &[&customer_id])
        .await?
        .iter()
        .map(|row| row.try_get::<_, i16>(0))
        .collect::<Result<HashSet<i16>, _>>()?;
    let kept: Vec<i16> = ids.iter().flatten().copied().collect();
    transaction
        .execute(statements.remove, &[&customer_id, &kept])
        .await?;
    for (index, id) in ids.iter().enumerate() {
        let Some(id) = id else {
            return Err(set.unknown(index));
        };
        if current.contains(id) {
            continue;
        }
        transaction
            .execute(statements.insert, &[&customer_id, id])
            .await
            .map_err(|error| set.insert_error(error, index))?;
    }

    let row = transaction
        .query_one(READ_REPLACED, &[&customer_id])
        .await?;
    let profile = profile_from(customer_id, &row)?;
    let audit_actor: Option<String> = row.try_get(2)?;
    if audit_actor.as_deref() != Some(actor.sub.as_str()) {
        tracing::error!("The replace does not carry its caller as the audit actor");
        return Err(ApiError::new(
            500,
            "internal_error",
            "The service could not complete the request.",
        ));
    }
    transaction.commit().await?;
    Ok(Replaced {
        profile,
        audit_actor,
    })
}

fn path_customer(ctx: &Ctx) -> Result<Uuid, ApiError> {
    ctx.path_uuid("customer_id").ok_or_else(customer_not_found)
}

async fn get_preference_profile(ctx: Ctx) -> Result<Reply, ApiError> {
    let customer_id = path_customer(&ctx)?;
    let profile = read_profile(ctx.db(), customer_id)
        .await?
        .ok_or_else(customer_not_found)?;
    Ok(profile.representation().into())
}

async fn put_set(ctx: Ctx, set: PreferenceSet) -> Result<Reply, ApiError> {
    let customer_id = path_customer(&ctx)?;
    let empty = JsonValue::Null;
    let ids = submitted_ids(set, ctx.body.as_ref().unwrap_or(&empty))?;
    let actor = ctx
        .principal()
        .ok_or_else(|| ApiError::new(401, "invalid_token", "The bearer token is invalid."))?;
    let replaced = replace(ctx.db(), actor, customer_id, set, &ids).await?;
    Ok(replaced.profile.representation().into())
}

async fn replace_preferred_channels(ctx: Ctx) -> Result<Reply, ApiError> {
    put_set(ctx, PreferenceSet::PreferredChannels).await
}

async fn replace_interest_categories(ctx: Ctx) -> Result<Reply, ApiError> {
    put_set(ctx, PreferenceSet::InterestCategories).await
}

async fn database_ready(db: Arc<Database>) -> bool {
    let Ok(connection) = db.connection().await else {
        return false;
    };
    connection.query(READY, &[]).await.is_ok()
}

pub fn router(config: KitConfig, contracts: &str) -> Result<Router, Box<dyn std::error::Error>> {
    Ok(Kit::new(SERVICE, SUFFIX, contracts, config)?
        .operation("getPreferenceProfile", get_preference_profile)?
        .operation("replacePreferredChannels", replace_preferred_channels)?
        .operation("replaceInterestCategories", replace_interest_categories)?
        .ready_check(database_ready)
        .into_router())
}

async fn redis_connection(
    redis_url: &str,
    redis_password: Option<String>,
) -> redis::RedisResult<redis::aio::MultiplexedConnection> {
    let info = service_redis_info(redis_url, SUFFIX, redis_password)?;
    redis::Client::open(info)?
        .get_multiplexed_async_connection()
        .await
}

/// The kit's outbox relay for `stream:preferences.v1.changed`. The first
/// connection is retried every second, so the service comes up during a Redis
/// outage; after that the kit rebuilds the connection after every outage
/// (#579). Protected operations still fail closed through the kit.
pub async fn run_relay(database_url: &str, redis_url: &str, redis_password: Option<String>) {
    let connection = loop {
        match redis_connection(redis_url, redis_password.clone()).await {
            Ok(connection) => break connection,
            Err(error) => {
                tracing::error!("Preferences outbox relay connection failed: {error}");
                tokio::time::sleep(Duration::from_secs(1)).await;
            }
        }
    };
    let relay = match OutboxRelay::new(database_url, SUFFIX, [STREAM], connection) {
        Ok(relay) => relay,
        Err(error) => {
            tracing::error!("The preferences outbox relay is misconfigured: {error}");
            return;
        }
    };
    relay
        .run_with_reconnect(|| redis_connection(redis_url, redis_password.clone()))
        .await;
}

async fn relay(config: KitConfig) {
    let Some(database_url) = config.database_url else {
        tracing::error!("DATABASE_URL is missing; the preferences outbox relay is disabled");
        return;
    };
    run_relay(
        &database_url,
        &config.redis_state_url,
        config.redis_password,
    )
    .await;
}

pub async fn serve(config: KitConfig, contracts: &str) -> Result<(), Box<dyn std::error::Error>> {
    tokio::spawn(relay(config.clone()));
    let app = router(config, contracts)?;
    let listener = tokio::net::TcpListener::bind(("0.0.0.0", PORT)).await?;
    mosaiq_kit::serve(listener, app).await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn ids_keep_their_order_and_mark_keys_no_smallint_can_hold() {
        let body = json!({"channel_ids": [5, 1, 3, 40000, -1]});
        assert_eq!(
            submitted_ids(PreferenceSet::PreferredChannels, &body).unwrap(),
            vec![Some(5), Some(1), Some(3), None, Some(-1)]
        );
        let empty = json!({"category_ids": []});
        assert!(submitted_ids(PreferenceSet::InterestCategories, &empty)
            .unwrap()
            .is_empty());
    }

    #[test]
    fn a_repeated_id_is_invalid_body_naming_the_field() {
        for (set, body) in [
            (
                PreferenceSet::PreferredChannels,
                json!({"channel_ids": [1, 2, 1]}),
            ),
            (
                PreferenceSet::InterestCategories,
                json!({"category_ids": [7, 7]}),
            ),
        ] {
            let error = submitted_ids(set, &body).unwrap_err();
            assert_eq!((error.status, error.code.as_str()), (422, "invalid_body"));
            assert_eq!(error.details, vec![Detail::new(set.field(), "duplicate")]);
        }
    }

    #[test]
    fn an_unknown_id_names_its_position() {
        let error = PreferenceSet::PreferredChannels.unknown(1);
        assert_eq!(
            (error.status, error.code.as_str()),
            (422, "unknown_channel")
        );
        assert_eq!(
            error.details,
            vec![Detail::new("channel_ids[1]", "unknown")]
        );
        let error = PreferenceSet::InterestCategories.unknown(0);
        assert_eq!(error.code, "unknown_category");
        assert_eq!(
            error.details,
            vec![Detail::new("category_ids[0]", "unknown")]
        );
    }
}
