//! Which stores a caller may read (ADR-0060). `STORE_MANAGER` and
//! `COMMERCIAL_SUPERVISOR` read only the stores assigned to them, taken from
//! `api_users.store_assignment_v1` inside the request that is served, with no
//! cache: a removed assignment stops working with the next request. Every
//! other role reads all stores.

use mosaiq_kit::{ApiError, Ctx, Detail};
use uuid::Uuid;

/// The roles confined to their assigned stores. The role vocabulary is the
/// service's own (ADR-0045 keeps it out of the kit).
pub const STORE_SCOPED_ROLES: [&str; 2] = ["STORE_MANAGER", "COMMERCIAL_SUPERVISOR"];

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Scope {
    /// The caller's role is confined to its stores.
    pub restricted: bool,
    /// The stores the report covers, ascending. Empty and `unrestricted` mean
    /// every store; empty and `restricted` mean none.
    pub store_ids: Vec<i32>,
    /// The store the caller named, when it did and may read it.
    pub named: Option<i32>,
}

impl Scope {
    /// The stores a SQL filter keeps: `None` means no store filter.
    pub fn filter(&self) -> Option<Vec<i32>> {
        if self.restricted {
            Some(self.store_ids.clone())
        } else {
            self.named.map(|store| vec![store])
        }
    }

    /// A store-scoped caller with no assigned store reads nothing.
    pub fn is_empty(&self) -> bool {
        self.restricted && self.store_ids.is_empty()
    }
}

pub fn forbidden_store() -> ApiError {
    ApiError::new(
        403,
        "forbidden_store",
        "The report names a store outside the caller's assigned stores.",
    )
    .with_details(vec![Detail::new("store_id", "not_assigned")])
}

/// The caller's scope for a report that may name `requested`.
pub async fn resolve(ctx: &Ctx, requested: Option<i32>) -> Result<Scope, ApiError> {
    let principal = ctx
        .principal()
        .ok_or_else(|| ApiError::new(401, "invalid_token", "The bearer token is invalid."))?;
    if !STORE_SCOPED_ROLES.contains(&principal.role.as_str()) {
        return Ok(Scope {
            restricted: false,
            store_ids: Vec::new(),
            named: requested,
        });
    }
    let assigned = match Uuid::parse_str(&principal.sub) {
        Ok(user_id) => {
            let connection = ctx.db().connection().await?;
            let rows = connection
                .query(
                    "SELECT store_id::integer FROM api_users.store_assignment_v1 \
                     WHERE user_id = $1 ORDER BY store_id",
                    &[&user_id],
                )
                .await?;
            rows.iter()
                .map(|row| row.try_get::<_, i32>(0))
                .collect::<Result<Vec<_>, _>>()?
        }
        // A subject that is no user has no assignment.
        Err(_) => Vec::new(),
    };
    match requested {
        Some(store) if !assigned.contains(&store) => Err(forbidden_store()),
        Some(store) => Ok(Scope {
            restricted: true,
            store_ids: vec![store],
            named: Some(store),
        }),
        None => Ok(Scope {
            restricted: true,
            store_ids: assigned,
            named: None,
        }),
    }
}
