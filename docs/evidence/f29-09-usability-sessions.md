# F29-09 (#893): the usability sessions, and what was done about them

> **Status: not yet run.** This document is the runbook and the report of the
> sessions that [`docs/usability-test-plan.md`](../usability-test-plan.md)
> (F29-08, #892) planned. Every `‹fill: …›` is filled during or after the
> sessions, and the [results](#4-results) are generated from the session
> records. They are not written by hand. Until a session has run, nothing below
> is evidence.

The plan fixed the tasks, roles, participants and measures. Neither this
runbook nor the tool changes any of them. Where the sessions had to depart from
the plan, the departure is written under [Deviations](#7-deviations-from-the-plan),
with its reason (plan §6).

## The criteria, and where each is answered

| Criterion of #893 | Answered by | Section |
|---|---|---|
| The sessions run on the deployed system, each role is covered, with results per task | Sessions on the F29-04 release (#894). `f29_09 check` refuses a record that leaves out one of its role's tasks. The coverage table names every role with no participant, or with stand-ins only | [1](#1-before-the-sessions), [4](#4-results) |
| Consent to record is on file, and no personal data of a participant is committed | Consent forms kept outside the repository, and noted per session. A record without consent to take notes is refused. Participants are `P-n` only, and free text that looks like an email address or a phone number is refused | [2.2](#22-consent), [3](#3-the-session-record) |
| A finding that matters has an issue, and the report says which were fixed | `findings.toml`, rendered in the findings table. `f29_09 check --final` refuses a blocker or major finding with no issue | [5](#5-findings-and-what-was-done) |
| The report says what the desktop gave analysts that the web does not | The participants' own words (`desktop_over_web`), then the team's reading of them | [6](#6-what-the-desktop-gives-an-analyst-that-the-web-does-not) |

Governing decisions: [`docs/process.md`](../process.md) §6,
[ADR-0008](../adr/0008-the-instance-keeps-the-demonstration-accounts-deactivated.md),
[ADR-0041](../adr/0041-android-and-electron-clients-run-different-processes-over-different-formats.md),
[ADR-0060](../adr/0060-four-store-profiles-map-to-four-roles-and-services-read-each-users-stores-per-request.md).

## Who does what

| Step | Who | Where |
|---|---|---|
| 1. The instance, accounts and data | Instance operator (Marcelo), with the moderator | instance, workstation |
| 2. Each session | A moderator, plus a note-taker where one is available, never a participant for a client they built | the participant's device or a team device |
| 3. The session record, the same day | The moderator, read by a second team member | `docs/evidence/f29-09/sessions/` |
| 4. The results | Anyone, by running `render --write` | this file |
| 5. Findings, issues and fixes | The team. Each fix goes in its own pull request | GitHub |

## 1. Before the sessions

Each of these must hold before the first session, and the record says when it
was checked.

1. **The release.** #894 (F29-04) is done, and the instance runs it. The
   version the sessions test: ‹fill: tag or commit›, checked on ‹fill: date›.
2. **The instance answers.** Only the project owner can start
   `mosaiq-deployment-vm`. The checks are the ones in
   [F20-01's "Before the run"](f20-01-android-sale.md#before-the-run), items 3
   and 4, for every service the tasks touch.
3. **The clients.**
   - Android: the release APK of the identified version, installed on
     ‹fill: device(s)›.
   - Electron: the console of the identified version, pointed at the instance
     with `settings.json` as in
     [F20-02 step 2](f20-02-electron-sale-campaign.md#2-settingsjson), on
     ‹fill: workstation(s)›.
4. **The accounts.** The seeded accounts are deactivated on the instance
   (ADR-0008). Each participant therefore signs in with an account created
   through users-service, the same way as
   [F20-01 step 1](f20-01-android-sale.md#1-create-the-account). There is one
   account per slot, named after the slot ("F29-09 P-3"), never after the
   person. Floor accounts are assigned to the session store (ADR-0060).
   Passwords are generated, handed to the participant on paper, kept only in
   the team's password manager, and the account is deactivated after the
   session.
5. **The data for each task.** Each task needs something on the instance to
   find. The moderator prepares it and writes it down here, before the first
   session. These values are synthetic data, never a real customer's (plan
   §3.2).

   | Task | What must exist | Prepared value |
   |---|---|---|
   | A-S1 | A synthetic customer with a loyalty QR, printed or on a second screen | ‹fill› |
   | A-S2 | A product barcode, available in the session store and in another one | ‹fill› |
   | A-S3 | An active promotion the cart's lines qualify for | ‹fill› |
   | A-S4 | A customer with a recommendation and its explanation | ‹fill› |
   | A-M1 | The session store's indicators for today | ‹fill› |
   | A-M2 | An active campaign with an audience and a validity | ‹fill› |
   | A-M3 | A product below its stock threshold in the session store | ‹fill› |
   | A-L1, A-L2 | Nothing beforehand. The participant creates a customer. Afterward, the moderator writes down its id so that it can be told apart from the synthetic ones | ‹fill› |
   | A-C1 | An open capture incident and the day's syncs | ‹fill› |
   | A-C2 | Promotions applied in the supervisor's stores today | ‹fill› |
   | A-E4 | A change the server refuses when a draft returns, chosen beforehand (for example, the draft's promotion is ended while the device is offline) | ‹fill› |
   | A-E5 | A way to expire the session mid-task (for example, revoking it from the administrator's side) | ‹fill› |
   | D-A1 | A sales file with known rejections (count and reason), kept outside the repository (`*.csv` is ignored) | ‹fill: count and reasons› |
   | D-A2 | Two model versions whose comparison has a right answer | ‹fill› |
   | D-A3 | Two periods with segment migrations | ‹fill› |
   | D-A4 | A published version with a drift verdict | ‹fill› |
   | D-A5 | A segment report that can be exported | ‹fill› |
   | D-M1 | Nothing beforehand. The draft campaign is written down afterward | ‹fill› |
   | D-M2, D-M3 | An experiment with a measured effect, and conversions with a reversal | ‹fill› |
   | D-I1 | Products whose demand outruns availability | ‹fill› |
   | D-U1 | A rule change in the audit log, with who and when | ‹fill› |
   | D-R1 | See [the administrator transfer](#the-administrator-transfer) | ‹fill› |
   | D-R2 | A floor user with no store | ‹fill› |

6. **The role can reach the task.** The plan says no participant is asked to
   use a module their role cannot open (plan §2.3). On `develop` at the time of
   writing (2026-11-23) these tasks do not meet that, and the moderator
   decides before the first session whether to change the plan (allowed until
   the first session, plan §6) or to record a [deviation](#7-deviations-from-the-plan):

   | Task | Role | What was found |
   |---|---|---|
   | A-S3, A-E2 | Store seller | The seller's Android profile has no Sale section (`FloorAccess.profiles`, ADR-0060), so a seller cannot build or confirm a sale. A-E2 asks the same of the loyalty officer and the commercial supervisor, who have no Sale section either |
   | A-E1 | Commercial supervisor | The profile (Check-in, Review, Outbox) has no customer profile to open |
   | D-A1 | Analyst | `createSaleImport` and `listSaleImports` are `ADMIN` only (`contracts/sales`) |
   | D-R2 | Administrator | The console has no store-assignment screen; `replaceStoreAssignment` is not called anywhere in `clients/desktop` |
   | A-M2, A-M3 | Store manager | Active campaigns show on Indicators with their discount and validity, not their audience. No screen shows a stock threshold |

   Check each row again against the release that is tested (#894), and strike
   the ones the release has fixed.

### The administrator transfer

D-R1 moves the one administrator role. The instance has exactly one
administrator, and its password never leaves the team. The task is therefore
run like this:

1. The instance operator signs the console in as the administrator. The
   participant never sees or types its password.
2. The participant transfers the role to a session account created for this
   purpose ("F29-09 administrator target").
3. Right after the session, the instance operator signs in as that account,
   transfers the role back, and deactivates it.
4. The record notes both transfers, with the time of each. At every moment
   there is one administrator.

If the team decides not to move the real administrator on the instance, D-R1
is not run there, and that is a [deviation](#7-deviations-from-the-plan).

## 2. Each session

### 2.1 The introduction

The moderator reads the same text to every participant (plan §5, step 2):

> Thank you for helping us. We are testing the application, not you. If
> something is hard, that is a problem with the application, and it is exactly
> what we need to find.
>
> I will read you some tasks, one at a time. Please do each one the way you
> would at work, and think aloud if you can. I will stay quiet while you work.
> I can't help during a task unless you have been stuck for a while. When you
> think a task is done, tell me.
>
> The data in this system is made up. Nothing you do affects a real customer.
> You can stop at any moment, without giving a reason.
>
> Before we start, here is the consent form. It asks whether we may take
> notes, and separately whether we may record the screen or your voice.

Language of the sessions: ‹fill: the language the introduction, the tasks and
the SUS are given in. If it is not English, the one translation used for every
participant is kept with the consent forms, and the record says so›.

### 2.2 Consent

- Written consent is collected before the first task. It covers taking notes
  and, separately, recording the screen or voice.
- The forms are kept by ‹fill: who›, in ‹fill: where, outside the repository›.
  They are not scanned into the repository or attached to an issue.
- Without consent to take notes, the session does not run.
- Without consent to record, nothing is recorded. The session runs on notes
  alone.
- Recordings, if any, stay where the forms are. No screenshot of a session
  shows the participant, their name, or another account than the slot's.

### 2.3 The tasks

1. Read each task of the role's table (plan §2.1 or §2.3), in order, exactly as
   written there.
2. Start the timer when you finish reading. Stop it at the end state, or when
   the participant gives up. At five minutes, end the task: it is failed.
3. Count the errors: wrong taps, wrong entries and wrong readings that the
   participant had to undo, or that you had to flag.
4. Give a hint only after two minutes stuck. Write down what you said. The task
   is then **assisted**.
5. Android only: run A-E1 to A-E5 (plan §2.2). You control the connection:
   ‹fill: how, for example airplane mode on the device, or the Wi-Fi of a
   hotspot you hold›.
6. Ask M-1 to M-5 with the figure on screen (plan §2.4). Mark a question
   `not-asked` when the participant's screens do not show that figure.
7. Hand over the SUS. Then ask: "What was the hardest part?" On the desktop,
   also ask: "What did this give you that the web application does not?"
   Write down both answers in the participant's own words. Leave out any name
   they mention.

### 2.4 The same day

Write the [session record](#3-the-session-record). Let a second team member
read it against the notes. Then run `check`. Deactivate the slot's account.

## 3. The session record

One file per participant per client:
`docs/evidence/f29-09/sessions/P-<n>-<client>.toml`. The tool refuses unknown
keys. This keeps a name, an email address or a free-form "about the
participant" field out of the repository. It also refuses free text that looks
like an email address or a phone number.

```toml
participant = "P-1"
client = "android"            # android | desktop
role = "SALES_ASSOCIATE"      # the role code of the account used
standing = "real"             # real: holds this role at work. stand-in: does not
built_this_client = false     # must be false (plan §3.1)
date = 2026-12-01
moderator = "github-login"
second_reader = "another-login"
# stopped = true              # if the participant stopped; record the tasks before it
sus = [4, 2, 4, 1, 4, 2, 5, 2, 4, 2]   # the ten answers, 1 to 5, in order
hardest = "Knowing whether the sale had gone through."
# desktop_over_web = "…"      # desktop sessions only
# Every key above goes before the first [table]: in TOML, a key written
# after [metrics] belongs to [metrics], and the tool refuses it there.

[consent]
notes = true                  # required
recording = false

[tasks.A-S1]
outcome = "complete"          # complete | assisted | failed
seconds = 74                  # 0 to 300. At 300 the task is failed
errors = 0
note = "Searched by surname after the QR."

[tasks.A-S2]
outcome = "assisted"
seconds = 205
errors = 2
hint = "Pointed at the store selector."   # required when assisted

[tasks.A-S3]
outcome = "complete"
seconds = 140
errors = 1

[tasks.A-S4]
outcome = "failed"
seconds = 300
errors = 0
note = "Did not find the explanation."

[tasks.A-E1]
outcome = "complete"
seconds = 30
errors = 0

[tasks.A-E2]
outcome = "complete"
seconds = 55
errors = 0

[tasks.A-E3]
outcome = "complete"
seconds = 40
errors = 0

[tasks.A-E4]
outcome = "failed"
seconds = 120
errors = 1
note = "Read the refusal as a network error."

[tasks.A-E5]
outcome = "complete"
seconds = 90
errors = 0

[metrics]                     # correct | partly | wrong | not-asked
M-1 = "partly"
M-2 = "wrong"
M-3 = "not-asked"
M-4 = "correct"
M-5 = "correct"
```

Findings go in `docs/evidence/f29-09/findings.toml`. One finding may have been
seen by several participants:

```toml
[[finding]]
id = "F-1"
severity = "major"            # blocker | major | minor (plan §4.3)
client = "android"
tasks = ["A-E4"]
seen_by = ["P-1", "P-2"]
summary = "A refused draft reads like a network error."
issue = 1201                  # required for blocker and major before the report is final
outcome = "fixed"             # open | fixed | not-fixing
fixed_by = 1210               # the pull request, when fixed
# reason = "…"                # required when not-fixing
```

The tool, from the repository root:

```sh
python -m tests.acceptance.f29_09 check            # after each session record
python -m tests.acceptance.f29_09 render --write   # regenerate section 4
python -m tests.acceptance.f29_09 check --final    # before #893 is closed
```

`check --final` also refuses a blocker or major finding with no issue, a fixed
finding with no pull request, a `‹fill` left anywhere in this file, and a
section 4 that no longer matches the records. `pytest` runs the same
consistency check on every pull request.

## 4. Results

Generated from the records. Do not edit between the markers.

<!-- f29_09:begin (generated by `python -m tests.acceptance.f29_09 render --write`) -->

#### Coverage of the roles

| Role | Client | Target | Real | Stand-in | Verdict |
|---|---|---|---|---|---|
| Store seller (`SALES_ASSOCIATE`) | Android | 2 | — | — | **not tested** |
| Store manager (`STORE_MANAGER`) | Android | 1 | — | — | **not tested** |
| Loyalty officer (`LOYALTY_OFFICER`) | Android | 1 | — | — | **not tested** |
| Commercial supervisor (`COMMERCIAL_SUPERVISOR`) | Android | 1 | — | — | **not tested** |
| Analyst (`ANALYST`) | Desktop | 2 | — | — | **not tested** |
| Marketing (`MARKETING`) | Desktop | 1 | — | — | **not tested** |
| Inventory planner (`INVENTORY_PLANNER`) | Desktop | 1, shared with auditor and administrator | — | — | **not tested** |
| Auditor (`AUDITOR`) | Desktop | 1, shared with inventory planner and administrator | — | — | **not tested** |
| Administrator (`ADMIN`) | Desktop | 1, shared with inventory planner and auditor | — | — | **not tested** |

#### Participants

No session has been recorded yet.

#### Results per task, real participants

No real participant has been recorded.

#### Results per task, stand-ins (not evidence of a real role's experience)

No stand-in participant has been recorded.

#### SUS per client

| Client | n | Each score | Mean, everyone | Mean, real only |
|---|---|---|---|---|
| Android | 0 | — | — | — |
| Desktop | 0 | — | — | — |

#### Metric readings

No metric reading has been recorded yet.

#### Findings

No finding has been recorded yet.

#### What was hardest, in the participants' words

None recorded yet.

#### What the desktop gave them that the web does not, in their words

None recorded yet.

<!-- f29_09:end -->

## 5. Findings, and what was done

The table in section 4 lists every finding with its issue and outcome. This
section says what the team concluded from them, and why any blocker or major
finding was left unfixed:

‹fill›

## 6. What the desktop gives an analyst that the web does not

The instructor asked that the clients not duplicate the web. Section 4 quotes
what the desktop participants said, in their own words. This is the team's
reading of those answers, task by task. It counts only what the participants
did and said, not what the console can do in principle:

‹fill›

## 7. Deviations from the plan

The plan was not edited after the first session. Every way the sessions
departed from it is listed here, with its reason (plan §6):

‹fill: "None" if there were none›
