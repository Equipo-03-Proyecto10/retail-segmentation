# #357 — the experiment frame after assignment, and accidental repeat exposures

Run on a real PostgreSQL 16 (Docker), a clean load of the three scripts from this
branch, the services connected as `retail_app`, seeded data.

## Exposure

| Check | Result |
|---|---|
| 8 connections record an exposure for one assignment at the same moment (twice) | 1 row added each time; one call returns written, seven return "nothing added" |
| The same request repeated straight after | nothing added |
| A later exposure, once the previous one is older than the 60 second guard | recorded (exposures stay plural, ADR-0019) |
| The day before the experiment starts | refused |
| The day after it ends | refused |
| Its last day | recorded |
| Its campaign cancelled | refused, no row added |

The first attempt at the concurrency check proved nothing: the seed's own exposure was
younger than 60 seconds, so the guard blocked all eight. It was repeated after ageing the
existing rows.

## The frame and the campaign

- Through `update_experiment` on an experiment with assignments: changing the start date,
  the end date or the campaign is refused with "is fixed: experiment 1 already has 4
  assignments".
- `create_experiment` for a cancelled campaign is refused.
- Activating a campaign that ended on 2020-02-01 is refused.

## Decision recorded

The issue asked for "a single event per assignment". ADR-0019 says the monolith records
each later exposure separately and the schema comment says the same, so this change keeps
exposures plural and stops the accident (a repeat within 60 seconds) instead. If the team
wants exactly one, that is a schema change (a unique index) and an ADR superseding the
plural wording.

## Corrections after review

The first verification exposed a real boundary defect: the application server's
`date.today()` and PostgreSQL's `CURRENT_DATE` differed by a day on this machine. The
merged implementation now reads the business date from PostgreSQL on the same connection
that records the event. The single-customer and bulk exposure paths also share the date,
campaign-status and 60-second repeat guards; `tests/test_experiment_frame.py` covers both.

Nothing was changed on the instance. There are no 375/1440 px captures of the edit form
(three fields became read-only once an experiment has assignments).
