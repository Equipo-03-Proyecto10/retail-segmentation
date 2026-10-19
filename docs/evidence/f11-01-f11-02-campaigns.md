# F11-01 and F11-02 — The experiment schema and the campaign workflow

Evidence for F11-01 (#220) and F11-02 (#221), closed with no file and, for the
campaign workflow, no screenshot. F11-01 revises the campaign and experiment schema
so the database refuses an unsound experiment: one customer in two arms, two
controls, no conversion window, no data origin. F11-02 is the campaign lifecycle a
marketing user runs: draft, activation, completion or cancellation, against a stable
segment label.

No schema change was made and no application code was changed. The experiment
setup, assignment, exposure, conversion and uplift stories that build on this have
their own files: [F11-03](f11-03-experiment-setup.md),
[F11-04](f11-04-group-assignment.md), [F11-05](f11-05-experiment-exposure.md),
[F11-06](f11-06-experiment-conversion.md), [F11-07](f11-07-experiment-uplift.md).

## What was run, and against what

Branch `docs/443-analytics-evidence`, from `develop` at `f2b9c81`; the stack from
`docker compose -p ev443 -f compose.yaml -f compose.proxy.yaml up --build -d`, an
empty volume so the three scripts ran in order (PostgreSQL 16.14, NGINX 1.27.5 with
the production CSP, Chromium 153 through Playwright). The workflow was driven as the
marketing demonstration account `user10@mosaiq-demo.com` (`MARKETING`, which holds
`campaign.read` and `campaign.write`); the refusals as `ANALYST`
(`user14@`, read only), `STORE_MANAGER` (`user15@`, no `campaign.read`) and the
others by script. Captures are full-page at 375 px and 1440 px with the classic
scrollbar hidden, so the layout is exactly 375 and 1440 CSS pixels wide;
`scrollWidth` equalled `clientWidth` in every one.

What was added to the throwaway database, all through the page and nothing inserted
by hand for F11-02: the lifecycle was walked through twice. The first time, campaign
31 *Evidence win-back* (target `AT_RISK`) went draft → active → finished and campaign
32 *Evidence cancelled draft* (target `LOYAL`) went draft → cancelled; those walks
left their rows in the audit log, quoted below, but were not captured. The second
walk, campaigns 33 *Evidence screenshots win-back* (`AT_RISK`, draft → active →
finished) and 34 *Evidence screenshots cancelled draft* (`LOYAL`, draft → cancelled),
is the one the captures show. For the F11-01 activation test, five probe campaigns
and experiments were written to a second database, `scratch443`, a copy of
`retail`, and deleted after.

## F11-01 — the schema (#220)

| Acceptance criterion | Proof |
|---|---|
| One customer cannot be assigned to two groups of the same experiment; the database refuses | `experiment_assignment_experiment_id_customer_id_key`, below |
| Activation is refused unless the experiment has exactly one control and at least one treatment | **As written, no longer the rule; see below.** Today's rule, ADR-0028 and RN-24, run against real PostgreSQL |
| Exposure and conversion have durable relations of their own, apart from assignment | Their columns and keys, below |
| An experiment carries a positive conversion-window duration and a data-origin marker | The `CHECK` and `NOT NULL` refusals below |
| The seed loads every new or changed relation, and the three scripts run clean in order | Row counts below; the clean load of the three scripts is logged in [F7-01 and F7-02](f7-01-f7-02-segment-history-model.md) |
| `docs/data-model.md` covers the 4NF argument and the data dictionary for the new relations | See the last paragraph of this section |

```text
$ psql -d retail -f f11_01.sql       (every write inside BEGIN ... ROLLBACK; blank lines and BEGIN/ROLLBACK echoes removed)
== one customer cannot be in two groups of the same experiment (customer 1 is in group 1 of experiment 1; group 2 is the other arm)
ERROR:  duplicate key value violates unique constraint "experiment_assignment_experiment_id_customer_id_key"
DETAIL:  Key (experiment_id, customer_id)=(1, 00000000-0000-0000-0000-000000000001) already exists.
== the same customer in the same group twice is refused by the same constraint
ERROR:  duplicate key value violates unique constraint "experiment_assignment_experiment_id_customer_id_key"
DETAIL:  Key (experiment_id, customer_id)=(1, 00000000-0000-0000-0000-000000000001) already exists.
== a group of another experiment cannot be used for this experiment (the composite key ties group and experiment together)
ERROR:  insert or update on table "experiment_assignment" violates foreign key constraint "experiment_assignment_group_id_experiment_id_fkey"
DETAIL:  Key (group_id, experiment_id)=(4, 1) is not present in table "experiment_group".
== an experiment cannot have two control groups
ERROR:  duplicate key value violates unique constraint "ux_experiment_one_control"
DETAIL:  Key (experiment_id)=(1) already exists.
== a conversion window must be positive
ERROR:  new row for relation "experiment" violates check constraint "experiment_conversion_window_days_check"
DETAIL:  Failing row contains (9001, Window zero, null, conversion_rate, 2026-11-01, null, 0, OBSERVED).
== the data origin must be one of the three markers
ERROR:  new row for relation "experiment" violates check constraint "experiment_data_origin_check"
DETAIL:  Failing row contains (9001, Unmarked origin, null, conversion_rate, 2026-11-01, null, 7, REAL).
== a conversion window and an origin are mandatory (NOT NULL)
ERROR:  null value in column "conversion_window_days" of relation "experiment" violates not-null constraint
DETAIL:  Failing row contains (9001, No window, null, conversion_rate, 2026-11-01, null, null, null).
== exposure and conversion are relations of their own, apart from assignment (columns)
      table_name       |                             columns                              
-----------------------+------------------------------------------------------------------
 experiment_assignment | assignment_id, experiment_id, group_id, customer_id, assigned_at
 experiment_conversion | conversion_id, assignment_id, transaction_id, converted_at
 experiment_exposure   | exposure_id, assignment_id, exposed_at
(3 rows)
== the seed loads every new relation (rows per table; none of them is listed in sql/seed-exempt.txt)
       relation        | count 
-----------------------+-------
 experiment            |    30
 experiment_group      |    60
 experiment_assignment |   120
 experiment_exposure   |    30
 experiment_conversion |    30
 campaign              |    32
(6 rows)
```

Read in order: two refusals by the same unique constraint (the same customer in
the other arm, and in the same arm again), the composite foreign key that ties a
group to its own experiment, `ux_experiment_one_control`, the window check, the
origin check and the `NOT NULL`.

None of the experiment tables is in `sql/seed-exempt.txt` (a search of that file for
`experiment` and `campaign` finds nothing), and each carries 30 or more rows.

**The activation rule.** The criterion says the database or the service refuses
activation unless the experiment has exactly one control group and at least one
treatment group. The database half that survives is `ux_experiment_one_control`: a
second control is refused (above). The service half has been changed by ADR-0028
(F11-08, #342): a control is optional, and an experiment without one must have
exactly two treatment arms (`docs/business-rules.md` RN-24, which says so in as
many words). `activation_refusal` was run on real PostgreSQL through
`campaigns.transition`, against experiments of five shapes:

```text
$ python f11_activation.py          (scratch443)
no groups at all                           -> refused: Campaign 9101 cannot be activated: experiment 9101 (Probe experiment 9101) has no control group and must have exactly two treatment groups.
a control only                             -> refused: Campaign 9102 cannot be activated: experiment 9102 (Probe experiment 9102) has no treatment group.
one control and one treatment              -> activated (ACTIVE)
two treatments, no control (ADR-0028)      -> activated (ACTIVE)
three treatments, no control               -> refused: Campaign 9105 cannot be activated: experiment 9105 (Probe experiment 9105) has no control group and must have exactly two treatment groups.
```

So the rule observed is: refused with no treatment, refused with a control and no
treatment, allowed with a control and one treatment, allowed with two treatments and
no control, refused with three treatments and no control. That is the story's
intent (never an unsound experiment) with ADR-0028's two-arm design allowed; it is
not the story's literal wording, and the evidence says so rather than claiming the
literal wording.

**Data dictionary and 4NF.** [`../data-model.md`](../data-model.md) §1 lists the three
relations (assignment, exposure, conversion) and their relationships, §3 has a
dictionary entry for each (`experiment_assignment`, `experiment_exposure`,
`experiment_conversion`), §2.4 says why `experiment_assignment` carries a surrogate
key for physical-design reasons and is not a normalization step, and §4 records the
delete rules and the `UNIQUE (group_id, experiment_id)` that makes the composite
foreign key above possible.

## F11-02 — the campaign workflow (#221)

| Acceptance criterion | Proof |
|---|---|
| A draft campaign is activated: it has a target label, a start date and an end date, and the transition is recorded | The activation captures, the audit trigger's rows with the acting user, and `NOT NULL` on all three columns |
| An active campaign is completed or cancelled: recorded, and no further transition is permitted | Campaign 33 completed, then a cancel refused; campaign 34 cancelled from draft. Active → cancelled is covered by a test only |
| A campaign's target names a label code from the vocabulary, not one run's segment | The form's select lists the six `segment_label` codes; `campaign.label_code` is a foreign key to `segment_label` |
| An invalid transition is refused with a message, not ignored | Three refusals captured below |
| A profile without the permission is refused by the default-deny gate | `STORE_MANAGER` 403 captured; the matrix below |

| Step | 1440 px | 375 px |
|---|---|---|
| The list as `MARKETING`: status pills and, per status, the Edit, Activate, Complete and Cancel actions; campaigns 32 and 31 from the first walk-through at the top | [capture](f11-02-campaigns-list-1440.png) | [capture](f11-02-campaigns-list-375.png) |
| The new-campaign form: name, target label (a select over the vocabulary), start and end | [capture](f11-02-campaign-new-form-1440.png) | [capture](f11-02-campaign-new-form-375.png) |
| An end date before the start: 400, `End date cannot be before the start date.` | [capture](f11-02-campaign-invalid-dates-1440.png) | [capture](f11-02-campaign-invalid-dates-375.png) |
| Created as a draft: `Campaign created as a draft.`, campaign 33 `Draft` with Edit, Activate, Cancel | [capture](f11-02-campaign-draft-created-1440.png) | [capture](f11-02-campaign-draft-created-375.png) |
| Activated: `Campaign 33 is now active.`, now only Complete is offered | [capture](f11-02-campaign-activated-1440.png) | [capture](f11-02-campaign-activated-375.png) |
| Editing an active campaign: redirected with `Campaign 33 is active; only a draft can be edited.` | [capture](f11-02-campaign-edit-refused-1440.png) | [capture](f11-02-campaign-edit-refused-375.png) |
| An invalid move, activating an active campaign: 409, `Campaign 33 is active and cannot become active; from active it can only become cancelled or finished.` | [capture](f11-02-campaign-invalid-transition-1440.png) | [capture](f11-02-campaign-invalid-transition-375.png) |
| Finished is final: cancelling it is 409, `Campaign 33 is finished; no further transition is permitted.` | [capture](f11-02-campaign-finished-is-final-1440.png) | [capture](f11-02-campaign-finished-is-final-375.png) |
| A draft cancelled before it ever runs: `Campaign 34 is now cancelled.` | [capture](f11-02-campaign-draft-cancelled-1440.png) | [capture](f11-02-campaign-draft-cancelled-375.png) |
| Activation refused for a seeded draft whose dates had passed: 409, `Campaign 29 ended on 2026-03-01, so it cannot be activated; edit the draft's dates first.` | [capture](f11-02-campaign-activate-expired-1440.png) | [capture](f11-02-campaign-activate-expired-375.png) |
| The audit log filtered to campaigns: the walk-throughs' rows, by *Demo User 10* | [capture](f11-02-campaign-audit-1440.png) | [capture](f11-02-campaign-audit-375.png) |
| One audit entry, the activation of campaign 33: `status` changed `DRAFT` to `ACTIVE`, the five other fields unchanged | [capture](f11-02-campaign-audit-activation-1440.png) | [capture](f11-02-campaign-audit-activation-375.png) |
| `ANALYST`: the list is visible with no action column (read only) | [capture](f11-02-campaigns-analyst-read-only-1440.png) | [capture](f11-02-campaigns-analyst-read-only-375.png) |
| `STORE_MANAGER`: 403 on the list | [capture](f11-02-refused-store-manager-1440.png) | [capture](f11-02-refused-store-manager-375.png) |

Each transition is one conditional `UPDATE`, so the audit trigger records it
(RN-28, RN-31); the decision record on #221 says the same. From the database after
both walk-throughs:

```text
$ psql -d retail -f f11_02_audit.sql
== campaigns 31 and 32 (the first walk-through) and 33 and 34 (the captured one): each change recorded by the audit trigger, with the acting user
 audit_id | action | campaign |         actor          | status_before | status_after | label_code 
----------+--------+----------+------------------------+---------------+--------------+------------
     1449 | INSERT | 31       | user10@mosaiq-demo.com |               | DRAFT        | AT_RISK
     1450 | UPDATE | 31       | user10@mosaiq-demo.com | DRAFT         | ACTIVE       | AT_RISK
     1451 | UPDATE | 31       | user10@mosaiq-demo.com | ACTIVE        | FINISHED     | AT_RISK
     1452 | INSERT | 32       | user10@mosaiq-demo.com |               | DRAFT        | LOYAL
     1453 | UPDATE | 32       | user10@mosaiq-demo.com | DRAFT         | CANCELLED    | LOYAL
     1458 | INSERT | 33       | user10@mosaiq-demo.com |               | DRAFT        | AT_RISK
     1459 | UPDATE | 33       | user10@mosaiq-demo.com | DRAFT         | ACTIVE       | AT_RISK
     1460 | UPDATE | 33       | user10@mosaiq-demo.com | ACTIVE        | FINISHED     | AT_RISK
     1461 | INSERT | 34       | user10@mosaiq-demo.com |               | DRAFT        | LOYAL
     1462 | UPDATE | 34       | user10@mosaiq-demo.com | DRAFT         | CANCELLED    | LOYAL
(10 rows)
== the refusals wrote nothing: campaign 29 (activation refused: its dates had passed) has only its seed insert
 campaign | audit_rows 
----------+------------
 29       |          1
 31       |          3
 32       |          2
 33       |          3
 34       |          2
(5 rows)
== a campaign names a label code from the vocabulary (foreign key), not one run's segment
                        Table "public.campaign"
   Column    |          Type          | Collation | Nullable | Default 
-------------+------------------------+-----------+----------+---------
 campaign_id | integer                |           | not null | 
 name        | character varying(120) |           | not null | 
 label_code  | character varying(40)  |           | not null | 
 starts_on   | date                   |           | not null | 
 ends_on     | date                   |           | not null | 
 status      | character varying(20)  |           | not null | 
Indexes:
    "campaign_pkey" PRIMARY KEY, btree (campaign_id)
    "idx_campaign_label" btree (label_code)
Check constraints:
    "campaign_check" CHECK (ends_on >= starts_on)
    "campaign_status_check" CHECK (status::text = ANY (ARRAY['DRAFT'::character varying, 'ACTIVE'::character varying, 'FINISHED'::character varying, 'CANCELLED'::character varying]::text[]))
Foreign-key constraints:
    "campaign_label_code_fkey" FOREIGN KEY (label_code) REFERENCES segment_label(label_code) ON DELETE RESTRICT
Referenced by:
    TABLE "experiment" CONSTRAINT "experiment_campaign_id_fkey" FOREIGN KEY (campaign_id) REFERENCES campaign(campaign_id) ON DELETE SET NULL
Triggers:
    trg_audit_campaign AFTER INSERT OR DELETE OR UPDATE ON campaign FOR EACH ROW EXECUTE FUNCTION fn_audit('campaign_id')
```

Each campaign has its insert and its transitions, each with `user10@mosaiq-demo.com`
as the actor (campaign 31 and 33: insert, activate, complete; 32 and 34: insert,
cancel). The refusals wrote nothing: campaign 29, whose activation was refused, still
has only its seed insert. (The audit ids skip a few numbers: statements rolled back
by the schema checks in this file and elsewhere consumed ids.)

The profiles that may and may not, from the live matrix in
[F4-07 and F4-08](f4-07-f4-08-analytics-authorization.md), each profile signed in
through the form and each campaign route requested (`-` is a permitted write, which the
script does not send):

```text
route                                       perm                      ADMIN  ANALYST STORE_MA MARKETIN INVENTOR  AUDITOR CUSTOMER ANONYMOU
GET /campaigns/                             campaign.read               200      200      403      200      403      200      403      302
GET /campaigns/new                          campaign.write              200      403      403      200      403      403      403      302
POST /campaigns/new                         campaign.write                -      403      403        -      403      403      403      403
GET /campaigns/1/edit                       campaign.write              200      403      403      200      403      403      403      302
POST /campaigns/1/edit                      campaign.write                -      403      403        -      403      403      403      403
POST /campaigns/1/activate                  campaign.write                -      403      403        -      403      403      403      403
```

## Tests

```text
$ pytest tests/test_campaigns.py tests/test_experiments.py tests/test_experiment_assignment.py
130 passed in 2.15s
```

For F11-02: `test_a_permitted_transition_is_one_guarded_update[...]` (all four allowed
moves), `test_an_illegal_transition_is_refused_with_a_message[...]` (six illegal
ones), `test_a_closed_campaign_says_no_further_transition_is_permitted`,
`test_a_campaign_is_created_as_a_draft_naming_a_label_code`,
`test_a_label_outside_the_vocabulary_is_a_field_error`,
`test_only_a_draft_can_be_edited`,
`test_a_profile_without_campaign_write_is_refused[...]`,
`test_read_only_profiles_see_the_list_but_no_controls[ANALYST]`.

## What was not run

- Active → cancelled was not driven on the page; the seed has active campaigns
  that could have been cancelled, but that would have changed seeded data for no
  new information. `test_a_permitted_transition_is_one_guarded_update[ACTIVE-cancel-CANCELLED]`
  covers it.
- The audience-size preview that F11-02's data model lists as reading
  `customer_segment_history` is not built. The decision comment on #221 records it as
  open ("either a follow-up or a deliberate omission"); this evidence does not
  decide it.
- An invalid transition from two browsers at once ("changed while you were
  working") was not provoked; `test_a_lost_race_is_refused_not_ignored` covers it.
- Nothing was run on the Compute Engine instance.

## The scripts

<details><summary><code>f11_01.sql</code></summary>

```sql
\pset pager off
\echo '== one customer cannot be in two groups of the same experiment (customer 1 is in group 1 of experiment 1; group 2 is the other arm)'
BEGIN;
INSERT INTO experiment_assignment (experiment_id, group_id, customer_id) VALUES (1, 2, '00000000-0000-0000-0000-000000000001');
ROLLBACK;
\echo '== the same customer in the same group twice is refused by the same constraint'
BEGIN;
INSERT INTO experiment_assignment (experiment_id, group_id, customer_id) VALUES (1, 1, '00000000-0000-0000-0000-000000000001');
ROLLBACK;
\echo '== a group of another experiment cannot be used for this experiment (the composite key ties group and experiment together)'
BEGIN;
INSERT INTO experiment_assignment (experiment_id, group_id, customer_id) VALUES (1, 4, '00000000-0000-0000-0000-000000000011');
ROLLBACK;
\echo '== an experiment cannot have two control groups'
BEGIN;
INSERT INTO experiment_group (group_id, experiment_id, kind, name, treatment_description) VALUES (9001, 1, 'CONTROL', 'Second control', 'No contact');
ROLLBACK;
\echo '== a conversion window must be positive'
BEGIN;
INSERT INTO experiment (experiment_id, name, target_metric, starts_on, conversion_window_days, data_origin) VALUES (9001, 'Window zero', 'conversion_rate', '2026-11-01', 0, 'OBSERVED');
ROLLBACK;
\echo '== the data origin must be one of the three markers'
BEGIN;
INSERT INTO experiment (experiment_id, name, target_metric, starts_on, conversion_window_days, data_origin) VALUES (9001, 'Unmarked origin', 'conversion_rate', '2026-11-01', 7, 'REAL');
ROLLBACK;
\echo '== a conversion window and an origin are mandatory (NOT NULL)'
BEGIN;
INSERT INTO experiment (experiment_id, name, target_metric, starts_on) VALUES (9001, 'No window', 'conversion_rate', '2026-11-01');
ROLLBACK;
\echo '== exposure and conversion are relations of their own, apart from assignment (columns)'
SELECT table_name, string_agg(column_name, ', ' ORDER BY ordinal_position) AS columns
  FROM information_schema.columns
 WHERE table_schema = 'public' AND table_name IN ('experiment_assignment', 'experiment_exposure', 'experiment_conversion')
 GROUP BY table_name ORDER BY table_name;
\echo '== the seed loads every new relation (rows per table; none of them is listed in sql/seed-exempt.txt)'
SELECT 'experiment' AS relation, count(*) FROM experiment UNION ALL SELECT 'experiment_group', count(*) FROM experiment_group
UNION ALL SELECT 'experiment_assignment', count(*) FROM experiment_assignment UNION ALL SELECT 'experiment_exposure', count(*) FROM experiment_exposure
UNION ALL SELECT 'experiment_conversion', count(*) FROM experiment_conversion UNION ALL SELECT 'campaign', count(*) FROM campaign;
```

</details>

<details><summary><code>f11_activation.py</code></summary>

```python
"""F11-01 AC2 / RN-24: which experiment shapes may a campaign be activated with?  (scratch database copy, real PostgreSQL)"""
import sys
sys.path.insert(0, "<repo>")
import psycopg
from web.services import campaigns
owner = psycopg.connect("postgresql://postgres:postgres@127.0.0.1:5432/scratch443", autocommit=True)
app = psycopg.connect("postgresql://retail_app:retail_app@127.0.0.1:5432/scratch443")

CASES = [  # (campaign id, description, groups)
    (9101, "no groups at all", []),
    (9102, "a control only", ["CONTROL"]),
    (9103, "one control and one treatment", ["CONTROL", "TREATMENT"]),
    (9104, "two treatments, no control (ADR-0028)", ["TREATMENT", "TREATMENT"]),
    (9105, "three treatments, no control", ["TREATMENT", "TREATMENT", "TREATMENT"]),
]
gid = 9100
with owner.cursor() as cur:
    for cid, _, groups in CASES:
        cur.execute("DELETE FROM experiment WHERE experiment_id = %s", (cid,))
        cur.execute("DELETE FROM campaign WHERE campaign_id = %s", (cid,))
        cur.execute("INSERT INTO campaign (campaign_id, name, label_code, starts_on, ends_on, status) VALUES (%s, %s, 'LOYAL', '2026-10-20', '2026-12-31', 'DRAFT')", (cid, f"Probe {cid}"))
        cur.execute("INSERT INTO experiment (experiment_id, name, campaign_id, target_metric, starts_on, conversion_window_days, data_origin) VALUES (%s, %s, %s, 'conversion_rate', '2026-10-20', 7, 'OBSERVED')", (cid, f"Probe experiment {cid}", cid))
        for kind in groups:
            gid += 1
            cur.execute("INSERT INTO experiment_group (group_id, experiment_id, kind, name, treatment_description) VALUES (%s, %s, %s, %s, 'probe')", (gid, cid, kind, f"{kind.title()} {gid}"))
for cid, description, groups in CASES:
    try:
        status = campaigns.transition(app, cid, "activate")
        print(f"{description:42} -> activated ({status})")
    except campaigns.InvalidTransition as refusal:
        print(f"{description:42} -> refused: {refusal}")
with owner.cursor() as cur:
    for cid, _, _ in CASES:
        cur.execute("DELETE FROM experiment WHERE experiment_id = %s", (cid,))
        cur.execute("DELETE FROM campaign WHERE campaign_id = %s", (cid,))
```

</details>

<details><summary><code>f11_02_audit.sql</code></summary>

```sql
\pset pager off
\echo '== campaigns 31 and 32 (the first walk-through) and 33 and 34 (the captured one): each change recorded by the audit trigger, with the acting user'
SELECT a.audit_id, a.action, a.entity_pk AS campaign, u.email AS actor,
       a.data_before->>'status' AS status_before, a.data_after->>'status' AS status_after, a.data_after->>'label_code' AS label_code
  FROM audit_log a LEFT JOIN app_user u ON u.user_id = a.user_id
 WHERE a.entity = 'campaign' AND a.entity_pk IN ('31', '32', '33', '34') ORDER BY a.audit_id;
\echo '== the refusals wrote nothing: campaign 29 (activation refused: its dates had passed) has only its seed insert'
SELECT entity_pk AS campaign, count(*) AS audit_rows FROM audit_log WHERE entity = 'campaign' AND entity_pk IN ('29', '31', '32', '33', '34') GROUP BY 1 ORDER BY 1;
\echo '== a campaign names a label code from the vocabulary (foreign key), not one run''s segment'
\d campaign
```

</details>
