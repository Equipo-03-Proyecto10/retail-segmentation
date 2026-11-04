# Usability test plan

The plan for the sessions of F29-09 (#893), written before any of them runs
(F29-08, #892). It fixes the tasks, the participants' roles and the measures, so
that the sessions measure what people can do and not what they say they like.

The sessions run on the deployed system, on the release of F29-04 (#894), against
the Android client (JSON) and the Electron console (XML). They do not run against
the web system, which the third delivery leaves as it is.

## 1. What the sessions answer

1. Can each role finish its daily work with the client built for it, without help?
2. Does a client behave in a way the participant can understand when the connection
   fails, and when work was done offline?
3. Do the participants read the metrics the clients show correctly?
4. What does the desktop console give an analyst that the web does not? (The
   instructor's "do not duplicate the web.")

## 2. Roles and tasks

Every task is a real operation on the deployed system, with the data the instance
holds. A task is complete only when its end state is observed (a row, a screen, a
refusal), not when the participant says it is done. Task ids are stable so that the
report can show results per task.

### 2.1 Android: daily commercial work

Roles follow ADR-0060. Each participant uses the profile's own account, assigned to
at least one store.

| Role | Id | Task | Observed end state |
|---|---|---|---|
| Store seller | A-S1 | Identify a customer by loyalty QR, then by search | The permitted profile is shown, and nothing the role must not see |
| | A-S2 | Find a product by barcode and say where it is available | Availability for the store and channel is read out correctly |
| | A-S3 | Build a cart with a valid discount and confirm the sale | One sale row exists, with the discount applied |
| | A-S4 | Show the customer a recommendation and say why it was offered | The participant restates the explanation shown |
| Store manager | A-M1 | Say how the store is doing today from the local indicators | The figures are read correctly (see §4.3) |
| | A-M2 | Find an active campaign and say whom it applies to | The audience and validity are stated correctly |
| | A-M3 | Find the stock of a product below its threshold | The right product and quantity |
| Loyalty officer | A-L1 | Register a new customer and record consent | The customer and consent rows exist |
| | A-L2 | Change a preference, then withdraw consent | The new state shows, and personalization stops |
| Commercial supervisor | A-C1 | Review the incidents and syncs of the day | The participant names the open incident |
| | A-C2 | Say how many promotions were applied, in aggregate | The figure is read correctly |

### 2.2 Android: connection errors and offline work

Every Android participant also does these tasks, with the connection controlled by
the moderator.

| Id | Task | Observed end state |
|---|---|---|
| A-E1 | With the connection cut, try to open the customer's profile | The participant says what happened and what to do next, from the screen alone |
| A-E2 | With the connection cut, confirm a sale | The sale shows as pending, not as confirmed |
| A-E3 | Reconnect and wait for the sync | The participant says whether the sale went through |
| A-E4 | With a draft the server refuses on return (the moderator invalidates it), read the outcome | The participant says which draft failed and why |
| A-E5 | With the session expired, continue working | The participant signs in again and loses no draft |

### 2.3 Desktop: specialized analysis

The console's roles are the ones the permission matrix gives each module. No
participant is asked to use a module their role cannot open.

| Role | Id | Task | Observed end state |
|---|---|---|---|
| Analyst | D-A1 | Import a sales file and say what was rejected and why | The rejection count and reason match the file |
| | D-A2 | Compare two model versions and say which fits better, and on which criterion | A criterion is named, and the winner is right |
| | D-A3 | Find the customers who moved segment between two periods | The migration matrix is read correctly |
| | D-A4 | Say whether the data has drifted since the version was published | The participant states the verdict and the evidence |
| | D-A5 | Export a segment report to Excel | A file the participant opens and reads |
| Marketing | D-M1 | Draft a campaign with an audience, a budget and a validity | The draft exists in the right state |
| | D-M2 | Read an A/B experiment and say whether the campaign worked | The participant states the effect with its uncertainty, not only a rate |
| | D-M3 | Say how many conversions the campaign earned, and what was not counted | Reversals are mentioned |
| Inventory planner | D-I1 | Find the products whose demand outruns availability | The right products |
| Auditor | D-U1 | Find who changed a rule, and when | The correct audit entry |
| Administrator | D-R1 | Transfer the administrator role | One administrator remains, and the participant says so |
| | D-R2 | Assign a store to a floor user | The assignment shows, and the user can work in that store |

### 2.4 Understanding of metrics

A separate short check, asked with the figure on screen, for both clients:

| Id | Question | Reading that counts as correct |
|---|---|---|
| M-1 | "What does this segment name mean for the customer?" | The participant describes the behavior, not just repeats the name |
| M-2 | "Is this increase caused by the campaign?" | Distinguishes attribution from measured effect |
| M-3 | "How sure are we of this number?" | Uses the interval or the sample size shown |
| M-4 | "Why is this product not offered?" | Names stock, consent or restriction |
| M-5 | "Would you act on this segment change?" | States a reason the data supports |

A wrong reading is a finding against the screen, not against the participant.

## 3. Participants

### 3.1 Who takes part

Whether real store staff are available is open (Q-19 in
[`scope-delivery-3.md`](scope-delivery-3.md)). The plan therefore does not assume
it. The report states for each participant:

- the role they were tested in, and
- whether they hold that role in their work (**real**), or stand in for it
  (**stand-in**).

A stand-in is never counted as evidence of a real role's experience, and the report
says so wherever it uses their results. If no real participant exists for a role,
the report says that the role was not tested by someone who does the work.

| Slot | Role | Client | Target |
|---|---|---|---|
| P-1 to P-2 | Store seller | Android | 2 |
| P-3 | Store manager | Android | 1 |
| P-4 | Loyalty officer | Android | 1 |
| P-5 | Commercial supervisor | Android | 1 |
| P-6 to P-7 | Analyst | Desktop | 2 |
| P-8 | Marketing | Desktop | 1 |
| P-9 | Administrator, inventory planner or auditor | Desktop | 1 |

Five or more participants for a client find most problems of its main flows, so the
Android client has five and the desktop console four. The targets are a floor: a role
with no participant is stated, not filled with the team.

Team members who built the client are not participants for it. They may moderate
and take notes.

### 3.2 Consent and privacy

- Written consent to take notes, and separately to record the screen or voice, is
  collected before each session and kept outside the repository.
- A participant is `P-n` everywhere in the repository. No name, contact, face or
  voice, and no customer's real data, is committed.
- Sessions use the instance's synthetic customers (ADR-0052).
- A participant may stop at any time, and their session is then reported as stopped.

## 4. Measures

These are fixed here, before the sessions.

### 4.1 Per task

| Measure | Definition |
|---|---|
| Success | **Complete** (end state reached unaided), **assisted** (reached after a hint the moderator gave, noted), or **failed** (not reached, or gave up) |
| Time | Seconds from the end of the instructions to the end state or the give-up. Timed by the moderator, not the participant |
| Errors | Count of wrong taps, wrong entries and wrong readings that the participant had to undo or that the moderator had to flag |

A task is capped at five minutes. At the cap it is ended and recorded as failed.

### 4.2 Per participant

The **System Usability Scale** (SUS), ten items on a five-point scale, asked after
the last task and scored in the standard way: odd items score `answer − 1`, even
items `5 − answer`, the sum times 2.5, for 0 to 100. One SUS per participant per
client. With so few participants it is reported with the individual scores beside
the mean, never as a mean alone.

### 4.3 For the report

- Task success rate per task and per role, with the number of participants.
- Median time and error count per task.
- SUS per client, with each score.
- Metric readings (M-1 to M-5): correct, partly correct, wrong.
- Findings, each with a severity: **blocker** (the task cannot be done), **major**
  (done with difficulty or help), **minor** (noticed, no effect on the task).
  Every blocker and major finding gets an issue (F29-09).

No target for these measures is set to be passed. They are reported as they are.

## 5. Procedure

1. The moderator checks that the instance runs the release under test, and that
   the accounts and data for the tasks exist.
2. The moderator reads the same introduction to every participant, and collects
   consent.
3. The participant does the tasks in the order of their role's table, with the
   moderator silent except to read a task and to note. A hint is given only when the
   participant has been stuck for two minutes, and is noted.
4. Android participants do §2.2 with the moderator controlling the connection.
5. The metric questions of §2.4 follow, asked with the figure on screen.
6. The participant answers the SUS and says what was hardest, in their words.
7. The moderator writes the session up the same day, with a second team member
   reading the notes.

## 6. Results

Results are written to `docs/evidence/f29-09-usability-sessions.md` by F29-09. This
plan is not edited after the first session. A change made to it afterward is
written in that report as a deviation, with its reason.
