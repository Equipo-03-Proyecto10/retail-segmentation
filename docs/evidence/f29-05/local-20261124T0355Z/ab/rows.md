# F29-05 Campaña A/B: PostgreSQL rows

<a id="rows-1"></a>
## 1. the groups before

```text
 assignment_id |             customer_id              | group_id |   kind    
---------------+--------------------------------------+----------+-----------
            61 | 00000000-0000-0000-0000-000000000001 |       31 | CONTROL
            62 | 00000000-0000-0000-0000-000000000002 |       31 | CONTROL
            63 | 00000000-0000-0000-0000-000000000003 |       32 | TREATMENT
            64 | 00000000-0000-0000-0000-000000000004 |       32 | TREATMENT
(4 rows)
```

<a id="rows-2"></a>
## 2. what changed for each assigned customer since the assignment

```text
             customer_id              | label_then | segment_changes_since | consent_decisions_since 
--------------------------------------+------------+-----------------------+-------------------------
 00000000-0000-0000-0000-000000000001 | (null)     |                    31 |                       3
 00000000-0000-0000-0000-000000000002 | (null)     |                    31 |                       3
 00000000-0000-0000-0000-000000000003 | (null)     |                    31 |                       3
 00000000-0000-0000-0000-000000000004 | (null)     |                    31 |                       4
(4 rows)
```

<a id="rows-3"></a>
## 3. the statements tried, each inside a transaction that is rolled back

```text
-- as retail_app: the application role moves a customer to the other arm
UPDATE public.experiment_assignment SET group_id = $1 WHERE assignment_id = $2
-- with ['32', '61']
-- psql:<stdin>:5: ERROR:  permission denied for table experiment_assignment

-- as retail_app: the application role deletes an assignment
DELETE FROM public.experiment_assignment WHERE assignment_id = $1
-- with ['61']
-- psql:<stdin>:5: ERROR:  permission denied for table experiment_assignment

-- as svc_experiments: experimentation's own role moves a customer to the other arm
UPDATE public.experiment_assignment SET group_id = $1 WHERE assignment_id = $2
-- with ['32', '61']
-- psql:<stdin>:5: ERROR:  permission denied for table experiment_assignment

-- as svc_experiments: experimentation's own role deletes an assignment
DELETE FROM public.experiment_assignment WHERE assignment_id = $1
-- with ['61']
-- psql:<stdin>:5: ERROR:  permission denied for table experiment_assignment

-- as postgres: the owner moves a customer to the other arm
UPDATE public.experiment_assignment SET group_id = $1 WHERE assignment_id = $2
-- with ['32', '61']
-- psql:<stdin>:5: ERROR:  An assignment is never rewritten: UPDATE of customer 00000000-0000-0000-0000-000000000001 in experiment 16 is refused

-- as postgres: the owner deletes an assignment
DELETE FROM public.experiment_assignment WHERE assignment_id = $1
-- with ['61']
-- psql:<stdin>:5: ERROR:  An assignment is never rewritten: DELETE of customer 00000000-0000-0000-0000-000000000001 in experiment 16 is refused

-- as svc_experiments: experimentation's own role assigns the same customer to the other arm too
INSERT INTO public.experiment_assignment (experiment_id, group_id, customer_id) VALUES ($1, $2, $3::uuid)
-- with [16, '32', '00000000-0000-0000-0000-000000000001']
-- psql:<stdin>:5: ERROR:  duplicate key value violates unique constraint "experiment_assignment_experiment_id_customer_id_key"

-- as postgres: the owner renames an arm after its customers were assigned
UPDATE public.experiment_group SET name = name || ' (changed)' WHERE group_id = $1
-- with ['31']
-- psql:<stdin>:5: ERROR:  Experiment group 31 is fixed after assignment
```

<a id="rows-4"></a>
## 4. the groups after

```text
 assignment_id |             customer_id              | group_id |   kind    
---------------+--------------------------------------+----------+-----------
            61 | 00000000-0000-0000-0000-000000000001 |       31 | CONTROL
            62 | 00000000-0000-0000-0000-000000000002 |       31 | CONTROL
            63 | 00000000-0000-0000-0000-000000000003 |       32 | TREATMENT
            64 | 00000000-0000-0000-0000-000000000004 |       32 | TREATMENT
(4 rows)
```
