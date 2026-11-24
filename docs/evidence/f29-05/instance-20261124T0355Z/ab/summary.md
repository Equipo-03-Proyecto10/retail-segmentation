# F29-05 Campaña A/B

Started 2026-11-24T03:54:30Z, finished 2026-11-24T03:54:33Z.
21 checks, 21 held, 0 did not.

- **experiment:** `16`
- **reader:** `s1-f2905-analyst@example.test`
- **gateway:** `https://127.0.0.1`
- **marketing:** `s1-f2905-marketing@example.test`
- **data origin:** `SEEDED`
- **assignment rule:** `V0`
- **arms before:** `{'CONTROL': 2, 'TREATMENT': 2}`
- **segment changes since assignment:** `124`
- **consent decisions since assignment:** `13`
- **the customer the attempts target:** `00000000-0000-0000-0000-000000000001`
- **its arm:** `CONTROL (group 31); the other group: 32`

| Criterion | Case | Expected | Actual | Evidence | |
|---|---|---|---|---|---|
| Campaña A/B | the experiment is ASSIGNED | ASSIGNED | ASSIGNED | [1](exchanges.md#exchange-5) | ✓ |
| Campaña A/B | the service lists as many assignments as the experiment states | 4 | 4 | [1](exchanges.md#exchange-6) | ✓ |
| Campaña A/B | the service and the database give every customer the same arm | no difference | no difference | [1](exchanges.md#exchange-6), [2](rows.md#rows-1) | ✓ |
| Campaña A/B | the groups are the ones the seed assigned (its own rule, written down in f29_05) | no difference | no difference | [1](exchanges.md#exchange-6) | ✓ |
| Campaña A/B | since the assignment a customer's segment moved or a consent was decided (a change really happened) | at least one | 124 segment changes, 13 consent decisions | [1](rows.md#rows-2) | ✓ |
| Campaña A/B | assigning the experiment again is refused | 409 already_assigned | 409 already_assigned | [1](exchanges.md#exchange-7) | ✓ |
| Campaña A/B | PATCH on the experiment is refused: there is no such operation | 405 | 405 | [1](exchanges.md#exchange-8) | ✓ |
| Campaña A/B | DELETE on the experiment is refused: there is no such operation | 405 | 405 | [1](exchanges.md#exchange-9) | ✓ |
| Campaña A/B | PATCH on an assignment is refused: there is no such operation | 404 or 405 | 404 | [1](exchanges.md#exchange-10) | ✓ |
| Campaña A/B | DELETE on an assignment is refused: there is no such operation | 404 or 405 | 404 | [1](exchanges.md#exchange-11) | ✓ |
| Campaña A/B | the application role moves a customer to the other arm: refused | refused: permission denied or never rewritten | psql:<stdin>:5: ERROR:  permission denied for table experiment_assignment | [1](rows.md#rows-3) | ✓ |
| Campaña A/B | the application role deletes an assignment: refused | refused: permission denied or never rewritten | psql:<stdin>:5: ERROR:  permission denied for table experiment_assignment | [1](rows.md#rows-3) | ✓ |
| Campaña A/B | experimentation's own role moves a customer to the other arm: refused | refused: permission denied or never rewritten | psql:<stdin>:5: ERROR:  permission denied for table experiment_assignment | [1](rows.md#rows-3) | ✓ |
| Campaña A/B | experimentation's own role deletes an assignment: refused | refused: permission denied or never rewritten | psql:<stdin>:5: ERROR:  permission denied for table experiment_assignment | [1](rows.md#rows-3) | ✓ |
| Campaña A/B | the owner moves a customer to the other arm: refused | refused: never rewritten | psql:<stdin>:5: ERROR:  An assignment is never rewritten: UPDATE of customer 00000000-0000-0000-0000-000000000001 in experiment 16 is refused | [1](rows.md#rows-3) | ✓ |
| Campaña A/B | the owner deletes an assignment: refused | refused: never rewritten | psql:<stdin>:5: ERROR:  An assignment is never rewritten: DELETE of customer 00000000-0000-0000-0000-000000000001 in experiment 16 is refused | [1](rows.md#rows-3) | ✓ |
| Campaña A/B | experimentation's own role assigns the same customer to the other arm too: refused | refused: duplicate key value violates unique constraint | psql:<stdin>:5: ERROR:  duplicate key value violates unique constraint "experiment_assignment_experiment_id_customer_id_key" | [1](rows.md#rows-3) | ✓ |
| Campaña A/B | the owner renames an arm after its customers were assigned: refused | refused: fixed after assignment | psql:<stdin>:5: ERROR:  Experiment group 31 is fixed after assignment | [1](rows.md#rows-3) | ✓ |
| Campaña A/B | after the attempts, the service gives every customer the arm it gave before | no difference | no difference | [1](exchanges.md#exchange-12) | ✓ |
| Campaña A/B | after the attempts, the database holds the same assignments, groups and arms | no difference | no difference | [1](rows.md#rows-1), [2](rows.md#rows-4) | ✓ |
| Campaña A/B | the arms' ids are unchanged too | no difference | no difference | [1](exchanges.md#exchange-12) | ✓ |

## Versions

| Piece | Version |
|---|---|
| checkout commit | `(unavailable)` |
| checkout describe | `(unavailable)` |
| auth contract | `1.1.0` |
| experimentation contract | `1.2.1` |
| auth release | `/opt/mosaiq/services/auth/releases/0.1.4` |
| experimentation release | `/opt/mosaiq/services/experimentation/releases/0.8.1` |
| web commit | `50bf7d5c48336a39f89f34f9bb8d44a78057fb06` |
