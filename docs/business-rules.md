# Business rules

The invariants MOSAIQ holds true regardless of who is using it or which screen
they are on.

Each rule names **where it is enforced**, because that is the part that decides
whether it actually holds. A rule enforced only in the application is bypassed
by a direct `INSERT`; a rule enforced only in the database surfaces as an
unexplained error in the interface. Where both are needed, both are listed, and
where only one exists today, the gap is stated rather than glossed.

Rules marked **verified** have a negative case that was actually run against a
database — see
[`evidence/f2-07-integrity-verification.md`](evidence/f2-07-integrity-verification.md).

---

## Access and identity

### RN-01 — There is exactly one administrator
Creating a second administrator, or promoting a second user to the role, is
refused. So is demoting or deactivating the only one: the system is never left
without an administrator.

**Enforced:** application *and* a partial unique index on `app_user(role_id)
WHERE role_id = 1`. **Neither exists yet** — both halves are F4-02 (#70), and
`AGENTS.md` is explicit that one half alone does not count. The seed already
holds exactly one administrator, so the index applies to existing data when it
lands. · `RF-05`

### RN-02 — A user's email is unique and is an email address
No two accounts share an address, and an address without `@` is refused.

**Enforced:** `app_user_email_key` and `app_user_email_check`. **Verified** —
cases N3 and N7. · `RF-01`

### RN-03 — A password is never stored, logged or transmitted in the clear
Only an argon2id hash is stored. The database never hashes and never receives a
plaintext password.

**Enforced:** application (`argon2-cffi`, pinned in `web/requirements.txt`). The
schema cannot enforce this; a review reads the sign-in and user-management code
for it. · `RNF-04`

### RN-04 — A user who has history is deactivated, never deleted
Removing access must not remove the record of what that person did.

**Enforced:** application refuses deletion and offers deactivation.
`audit_log.user_id` is `ON DELETE SET NULL`, so even a direct deletion degrades
to an unattributed entry rather than destroying the entry. · `RF-09`

### RN-05 — Only the administrator runs the segment recalculation
It rewrites a column on every customer, so it is not an analyst's button.

**Enforced:** authorization middleware, F4-01 (#69). Not yet built. · `RF-12`

## Referential integrity

### RN-06 — A category with products cannot be deleted
**Enforced:** `product_category_id_fkey` `ON DELETE RESTRICT`. **Verified** —
case N13. The application turns the refusal into a message naming how many
products reference it. · `RF-07`

### RN-07 — A parent category with children cannot be deleted
The hierarchy is protected by the same rule as the product reference.

**Enforced:** `category_parent_category_id_fkey` `ON DELETE RESTRICT`.
**Verified** — case N16. · `RF-07`

### RN-08 — A customer with recorded sales cannot be deleted
Sales history is the basis of every segment; deleting the customer would orphan
it.

**Enforced:** `transaction_customer_id_fkey` `ON DELETE RESTRICT`. **Verified** —
case N14. · `RF-07`

### RN-09 — Deleting a sale removes its lines
A sale line has no meaning without its sale.

**Enforced:** `ON DELETE CASCADE` on `transaction_line`. **Verified** — case P4.

### RN-10 — A product SKU is unique
**Enforced:** `product_sku_key`. **Verified** — case N2. · `RF-06`

## Money and quantities

### RN-11 — Prices and totals are never negative
Product list price, sale total and sale line unit price are all zero or greater.

**Enforced:** `product_list_price_check`, `transaction_total_check`,
`transaction_line_unit_price_check`. **Verified** — case N6. · `RF-06`

### RN-12 — A sale line's quantity is greater than zero
A line with zero units is not a line.

**Enforced:** `transaction_line_quantity_check`. **Verified** — case N10.

### RN-13 — A sale line records the price actually charged
`transaction_line.unit_price` is what the customer paid, not
`product.list_price`. A later price change must not rewrite history.

**Enforced:** the model — the price is stored on the line rather than read
through the product. The application must never populate it by copying the
current list price at read time. Reviewed, not constrained. · `RF-10`

### RN-14 — Stock is never negative
**Enforced:** `inventory_quantity_on_hand_check`. · `RF-11`

## Campaigns, segments and experiments

### RN-15 — A campaign cannot end before it starts
**Enforced:** `campaign_check`. **Verified** — case N11.

### RN-16 — A campaign's status is one of four
`DRAFT`, `ACTIVE`, `FINISHED`, `CANCELLED`.

**Enforced:** `campaign_status_check`. **Verified** — case N8.

### RN-17 — A segment's validity cannot end before it starts
**Enforced:** `segment_check`.

### RN-18 — RFM bands run 1 to 5, and no minimum exceeds its maximum
**Enforced:** the six `segment_rule_*_check` column constraints and the
table-level `segment_rule_check`. **Verified** — case N9.

### RN-19 — An experiment group is control or treatment
**Enforced:** `experiment_group_kind_check`.

### RN-20 — A customer sits in at most one segment at a time
**Enforced:** the model — `customer.current_segment_id` is a single nullable
column, so a second concurrent segment is not expressible.

This rule is **correct today and wrong tomorrow.** [`roadmap.md`](roadmap.md)
requires segment assignments to be kept as history rather than overwritten, and
when the segment-history module lands, this column is replaced by an assignment
table where the same rule becomes "at most one *open* assignment" and needs a
real constraint. See
[ADR-0004](adr/0004-model-ahead-of-the-deferred-segmentation-modules.md).

### RN-21 — A customer with no sales in the window is unassigned
The recalculation clears the segment rather than leaving a stale one. An empty
segment is information; a wrong one is not.

**Enforced:** application, F3-10 (#102). Not yet built. · `RF-12`

## Audit

### RN-22 — Every change to a catalog or a business rule is recorded
Inserts, updates and deletes on `category`, `product`, `store`, `customer`,
`app_user`, `segment`, `segment_rule`, `campaign` and `experiment` all write an
audit entry.

Individual sales are deliberately **not** audited: their history already lives
in `transaction` and `transaction_line`, and auditing them would double the
write cost of the busiest table in the model.

**Enforced:** nine `AFTER INSERT OR UPDATE OR DELETE` triggers calling
`fn_audit()`. **Verified** — case P2. · `RF-14`

### RN-23 — The audit log never carries a credential
**Enforced:** `fn_audit()` strips `password_hash` from both payloads before
writing. **Verified** — case P3: zero hashes across 280 audit rows. · `RNF-17`

### RN-24 — The audit log is append-only
Nothing in the application updates or deletes an entry. Entries are kept
indefinitely as compliance evidence.

**Enforced:** application — the audit view offers no write action. Not
constrained in the schema, because the role that owns the schema must remain
able to archive. Reviewed. · `RF-14`

---

## Where the gaps are

Four rules have no enforcement yet, and all four are application-side:

| Rule | Waiting on |
|---|---|
| RN-01 exactly one administrator | F4-02 (#70) — **both** halves, and the schema half does not exist either |
| RN-03 password hashing | F3-03 (#63) |
| RN-05 only the administrator recalculates | F4-01 (#69) |
| RN-21 customers with no recent sales are unassigned | F3-10 (#102) |

RN-01 is the one to watch. It is a hard rule in `AGENTS.md`, it is a success
criterion in [`scope.md`](scope.md) §7, and today it holds only because the seed
happens to contain one administrator.
