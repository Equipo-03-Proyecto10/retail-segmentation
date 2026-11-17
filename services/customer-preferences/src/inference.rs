//! Interests inferred from net purchases (F21-18, ADR-0066, RN-34, RN-35).
//!
//! An inference run reads what a customer bought through
//! `api_sales.net_purchase_line_v1` and the category of each product through
//! `api_products.product_ref_v1`, and replaces the customer's rows of
//! `preferences.inferred_interest` as a whole. The strength of a category is
//! the percentage of the customer's net purchases in the window that include a
//! product of it, rounded half up; a category under 1% is not kept. A declared
//! interest is never written to, and a declared category is not inferred.
//!
//! Consent decides everything. A customer whose latest `PROFILING` decision is
//! not `GRANTED` (withdrawn, or never recorded: the log fails closed) has
//! nothing inferred, and the rows they had are deleted. The run holds the
//! customer's decision lock, the one a consent decision takes, so a withdrawal
//! and a run never interleave. A reader also returns nothing inferred unless
//! `PROFILING` and `PERSONALIZATION` are both granted at that moment, so a
//! withdrawal is never visible as stale data between two runs.

use std::time::Duration;

use mosaiq_kit::{ApiError, Ctx, Database, Reply, Representation, Value};
use tokio_postgres::GenericClient;
use uuid::Uuid;

use crate::consent::DECISION_LOCK_CLASS;
use crate::{customer_not_found, path_customer};

/// The window of a run when `INFERENCE_WINDOW_DAYS` does not say otherwise.
pub const DEFAULT_WINDOW_DAYS: i16 = 180;
/// How often the background job runs when `INFERENCE_INTERVAL_HOURS` does not
/// say otherwise.
pub const DEFAULT_INTERVAL_HOURS: u64 = 24;
/// The only source an inferred interest names.
pub const SOURCE: &str = "NET_PURCHASES";

const CUSTOMER_EXISTS: &str =
    "SELECT customer_id FROM api_customers.customer_ref_v1 WHERE customer_id = $1";
const LOCK_DECISIONS: &str = "SELECT pg_advisory_xact_lock($1, hashtext($2::uuid::text))";
const PROFILING_GRANTED: &str = "SELECT EXISTS (SELECT FROM api_preferences.consent_state_v1 AS c \
     WHERE c.customer_id = $1 AND c.purpose = 'PROFILING' AND c.decision = 'GRANTED')";
const DELETE_INFERRED: &str = "DELETE FROM preferences.inferred_interest WHERE customer_id = $1";
/// The consent decision just written is visible to this statement, since it
/// runs in the same transaction.
const DELETE_UNLESS_PROFILING: &str = "DELETE FROM preferences.inferred_interest AS i \
     WHERE i.customer_id = $1 AND NOT EXISTS ( \
         SELECT FROM api_preferences.consent_state_v1 AS c \
         WHERE c.customer_id = i.customer_id AND c.purpose = 'PROFILING' \
           AND c.decision = 'GRANTED')";
/// A sale counts once for a category however many of its lines are in it, so
/// the strength is the share of sales and not of lines. A sale reversed in full
/// has no row in the view and is not a purchase. The declared categories are
/// left out.
const INSERT_INFERRED: &str = "INSERT INTO preferences.inferred_interest \
     (customer_id, category_id, source, window_days, strength, inferred_at) \
     WITH purchases AS ( \
         SELECT DISTINCT n.transaction_id, p.category_id \
         FROM api_sales.net_purchase_line_v1 AS n \
         JOIN api_products.product_ref_v1 AS p ON p.product_id = n.product_id \
         WHERE n.customer_id = $1 \
           AND n.occurred_at >= now() - make_interval(days => $2::smallint::int)), \
     total AS (SELECT count(DISTINCT transaction_id) AS sales FROM purchases) \
     SELECT $1::uuid, u.category_id, 'NET_PURCHASES', $2::smallint, \
            round(100.0 * count(*) / t.sales)::smallint, now() \
     FROM purchases AS u CROSS JOIN total AS t \
     WHERE NOT EXISTS (SELECT FROM public.customer_interest_category AS d \
                       WHERE d.customer_id = $1 AND d.category_id = u.category_id) \
     GROUP BY u.category_id, t.sales \
     HAVING round(100.0 * count(*) / t.sales) >= 1";
const PROFILED_CUSTOMERS: &str = "SELECT c.customer_id FROM api_preferences.consent_state_v1 AS c \
     WHERE c.purpose = 'PROFILING' AND c.decision = 'GRANTED' ORDER BY c.customer_id";
/// Rows of customers who no longer grant PROFILING, whoever left them there.
const DELETE_ORPHANS: &str = "DELETE FROM preferences.inferred_interest AS i \
     WHERE NOT EXISTS (SELECT FROM api_preferences.consent_state_v1 AS c \
         WHERE c.customer_id = i.customer_id AND c.purpose = 'PROFILING' \
           AND c.decision = 'GRANTED')";
/// One statement, so one snapshot. The declared set always comes; the inferred
/// rows join only while both purposes are granted and leave out the declared
/// categories, so the two never overlap and a withdrawal shows at once.
const READ_INTERESTS: &str = "SELECT \
     ARRAY(SELECT d.category_id FROM public.customer_interest_category AS d \
           WHERE d.customer_id = r.customer_id ORDER BY d.category_id), \
     i.category_id, i.source, i.window_days, i.strength, i.inferred_at \
     FROM api_customers.customer_ref_v1 AS r \
     LEFT JOIN preferences.inferred_interest AS i ON i.customer_id = r.customer_id \
         AND (SELECT count(*) FROM api_preferences.consent_state_v1 AS c \
              WHERE c.customer_id = r.customer_id AND c.decision = 'GRANTED' \
                AND c.purpose IN ('PROFILING', 'PERSONALIZATION')) = 2 \
         AND NOT EXISTS (SELECT FROM public.customer_interest_category AS d \
                         WHERE d.customer_id = r.customer_id AND d.category_id = i.category_id) \
     WHERE r.customer_id = $1 \
     ORDER BY i.strength DESC, i.category_id";

/// One inferred interest, as the contract's `InferredInterest`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Inferred {
    pub category_id: i16,
    pub source: String,
    pub window_days: i16,
    pub strength: i16,
    pub inferred_at: chrono::DateTime<chrono::Utc>,
}

/// A customer's declared interests and the ones inferred next to them.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct InterestProfile {
    pub customer_id: Uuid,
    pub declared_category_ids: Vec<i16>,
    pub inferred_interests: Vec<Inferred>,
}

impl InterestProfile {
    pub fn representation(&self) -> Representation {
        let declared = self
            .declared_category_ids
            .iter()
            .copied()
            .map(Value::from)
            .collect();
        let inferred = self
            .inferred_interests
            .iter()
            .map(|interest| {
                Value::object([
                    ("category_id", Value::from(interest.category_id)),
                    ("source", Value::from(interest.source.clone())),
                    ("window_days", Value::from(interest.window_days)),
                    ("strength", Value::from(interest.strength)),
                    ("inferred_at", Value::from(interest.inferred_at)),
                ])
            })
            .collect();
        Representation::new(
            "InterestProfile",
            [
                ("customer_id", Value::from(self.customer_id.to_string())),
                ("declared_category_ids", Value::List(declared)),
                ("inferred_interests", Value::List(inferred)),
            ],
        )
    }
}

/// The interests of a customer; `None` when the customer does not exist.
pub async fn read_interests<C: GenericClient + Sync>(
    client: &C,
    customer_id: Uuid,
) -> Result<Option<InterestProfile>, ApiError> {
    let rows = client.query(READ_INTERESTS, &[&customer_id]).await?;
    let Some(first) = rows.first() else {
        return Ok(None);
    };
    let declared_category_ids: Vec<i16> = first.try_get(0)?;
    let mut inferred_interests = Vec::new();
    for row in &rows {
        let Some(category_id) = row.try_get::<_, Option<i16>>(1)? else {
            continue;
        };
        inferred_interests.push(Inferred {
            category_id,
            source: row.try_get(2)?,
            window_days: row.try_get(3)?,
            strength: row.try_get(4)?,
            inferred_at: row.try_get(5)?,
        });
    }
    Ok(Some(InterestProfile {
        customer_id,
        declared_category_ids,
        inferred_interests,
    }))
}

/// Run the inference for one customer over the last `window_days` days and
/// return how many interests it left. A customer who does not grant `PROFILING`
/// gets nothing, and the interests they had are deleted. 404 when the customer
/// does not exist.
pub async fn infer_customer(
    db: &Database,
    customer_id: Uuid,
    window_days: i16,
) -> Result<usize, ApiError> {
    let mut connection = db.connection().await?;
    let transaction = connection.transaction(None).await?;
    transaction
        .query_opt(CUSTOMER_EXISTS, &[&customer_id])
        .await?
        .ok_or_else(customer_not_found)?;
    transaction
        .execute(LOCK_DECISIONS, &[&DECISION_LOCK_CLASS, &customer_id])
        .await?;
    transaction
        .execute(DELETE_INFERRED, &[&customer_id])
        .await?;
    let granted: bool = transaction
        .query_one(PROFILING_GRANTED, &[&customer_id])
        .await?
        .try_get(0)?;
    let kept = if granted {
        transaction
            .execute(INSERT_INFERRED, &[&customer_id, &window_days])
            .await? as usize
    } else {
        0
    };
    transaction.commit().await?;
    Ok(kept)
}

/// Run the inference for every customer who grants `PROFILING`, then delete the
/// rows of any who do not. Returns how many customers were inferred for; one
/// customer's failure is logged and does not stop the others.
pub async fn infer_all(db: &Database, window_days: i16) -> Result<usize, ApiError> {
    let customers: Vec<Uuid> = {
        let connection = db.connection().await?;
        connection
            .query(PROFILED_CUSTOMERS, &[])
            .await?
            .iter()
            .map(|row| row.try_get(0))
            .collect::<Result<_, _>>()?
    };
    let mut done = 0;
    for customer in customers {
        match infer_customer(db, customer, window_days).await {
            Ok(_) => done += 1,
            Err(error) if error.status == 404 => {}
            Err(error) => tracing::error!("Inference failed for {customer}: {}", error.code),
        }
    }
    let connection = db.connection().await?;
    connection.execute(DELETE_ORPHANS, &[]).await?;
    Ok(done)
}

/// Delete a customer's inferred interests unless `PROFILING` is granted, inside
/// the transaction that records the consent decision.
pub(crate) async fn forget_unless_profiling<C: GenericClient + Sync>(
    client: &C,
    customer_id: Uuid,
) -> Result<(), ApiError> {
    client
        .execute(DELETE_UNLESS_PROFILING, &[&customer_id])
        .await?;
    Ok(())
}

pub(crate) async fn get_interest_profile(ctx: Ctx) -> Result<Reply, ApiError> {
    let customer_id = path_customer(&ctx)?;
    let connection = ctx.db().connection().await?;
    let profile = read_interests(&***connection, customer_id)
        .await?
        .ok_or_else(customer_not_found)?;
    Ok(profile.representation().into())
}

/// The window and the interval of the background job, from the environment.
pub fn job_settings() -> (bool, i16, Duration) {
    let enabled = std::env::var("INFERENCE_ENABLED").map_or(true, |value| value != "false");
    let window = std::env::var("INFERENCE_WINDOW_DAYS")
        .ok()
        .and_then(|value| value.parse::<i16>().ok())
        .filter(|days| (1..=3650).contains(days))
        .unwrap_or(DEFAULT_WINDOW_DAYS);
    let hours = std::env::var("INFERENCE_INTERVAL_HOURS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|hours| *hours > 0)
        .unwrap_or(DEFAULT_INTERVAL_HOURS);
    (enabled, window, Duration::from_secs(hours * 3600))
}

/// Infer for everyone, then again every interval, until the process ends.
pub async fn run_job(db: std::sync::Arc<Database>, window_days: i16, interval: Duration) {
    loop {
        match infer_all(&db, window_days).await {
            Ok(done) => tracing::info!("Inferred interests for {done} customers"),
            Err(error) => tracing::error!("The inference run failed: {}", error.code),
        }
        tokio::time::sleep(interval).await;
    }
}
