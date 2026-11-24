# F29-04 on the instance: requests and responses

<a id="exchange-1"></a>
## 1. GET /api/rfm-engine/openapi.yaml (version)

`X-Correlation-ID: f29-04-5b1d54bd32fc4455`, at 2026-11-24T03:57:10Z

```http
GET https://mosaiq.maxthecoder.online/api/rfm-engine/openapi.yaml
X-Correlation-ID: f29-04-5b1d54bd32fc4455


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f29-04-5b1d54bd32fc4455

openapi: 3.1.0
info:
  title: rfm-engine-service
  version: 1.2.1
  description: >-
    Keeps each customer's R, F and M current. A customer's profile is a function
    of their accepted, unreversed purchases under one rule-set version; it changes
    only for the customers an event touched, and equals a full recalculation of
    that version at the same instant (RN-65). A sale, a reversal, the passing of
    time, a published version or a verification triggers a recomputation of the
    customer from net purchases, never a delta, so a replay or a reordering
    cannot change a result (ADR-0063). Scoring uses the boundaries frozen in the
    rule set and never the population. rfm-engine serves three resources: the
    current profiles (`rfm-profiles`), the rule sets they are scored under
    (`rfm-rule-sets`, each a draft until it is published and immutable after) and the verification recalculations that compare the
    incremental result with a full one (`rfm-recalculations`). It does not
    serve segments: the customer's current segment is segments' resource, and
    rfm-engine reaches it only through the database function the segments
    boundary publishes.
security:
  - bearer: []
tags:
  - name: Profiles
  - name: RuleSets
  - name: Recalculations
  - name: Meta
paths:
  /v1/rfm-profiles:
    get:
      tags: [Profiles]
      summary: List current profiles
      description: >-
        Returns a page of the stored profiles, ordered by `customer_id`. A
        profile is never computed by a read: it is what the last event, scheduled
        pass or version change left (RN-65). `state`, `label_code` and
        `rule_set_id` filter on the stored values; `label_code` is rfm-engine's
        own result and is not the customer's current segment, which segments
        serves. A customer with no stored profile is `pending` and is listed only
        when `state=pending` is asked for.
      operationId: listRfmProfiles
      x-mosaiq-roles:
        - ADMIN
        - ANALYST
        - STORE_MANAGER
        - MARKETING
        - AUDITOR
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
        - $ref: '#/components/parameters/ProfileState'
        - $ref: '#/components/parameters/LabelCodeFilter'
        - $ref: '#/components/parameters/RuleSetIdFilter'
        - $ref: '#/components/parameters/Page'
        - $ref: '#/components/parameters/Size'
      responses:
        '200':
          description: A page of profiles.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/RfmProfilePage'
              examples:
                page:
                  externalValue: ./examples/profiles-page.json
            application/xml:
              schema:
                $ref: '#/components/schemas/RfmProfilePage'
              examples:
                page:
                  externalValue: ./examples/profiles-page.xml
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '422':
          $ref: '#/components/responses/ListInvalid'
        '429':
          $ref: '#/components/responses/RateLimited'
        '503':
          $ref: '#/components/responses/OperationUnavailable'
  /v1/rfm-profiles/{customer_id}:
    get:
      tags: [Profiles]
      summary: Get a customer's current profile
      description: >-
        Returns the customer's stored profile, which is always a 200 answer for
        a registered customer. `scored` carries the raw recency in whole days,
        frequency and monetary total, the three scores and the label, all under
        the rule set named by `rule_set_id` and as of `computed_at`.
        `via_fallback` is true when no band matched and the rule set's fallback
        label was taken (RN-46). `unassigned` is a customer with no net purchase
        in the window (RN-21), whether they never bought or every sale was
        reversed: `f_count` is 0, `m_total` is `0.00` and nothing else is set.
        `pending` means no profile has been computed yet and carries nothing
        more. `next_change_at` is the first instant the profile will change with
        no new event: when the oldest net purchase leaves the window or the
        recency crosses a frozen boundary. A customer absent from
        `api_customers.customer_ref_v1` is 404 `customer_not_found`. The
        customer id is the resource's own identity, one profile per customer.
      operationId: getRfmProfile
      x-mosaiq-roles:
        - ADMIN
        - ANALYST
        - STORE_MANAGER
        - MARKETING
        - AUDITOR
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
        - $ref: '#/components/parameters/CustomerId'
      responses:
        '200':
          description: The customer's profile.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/RfmProfile'
              examples:
                scored:
                  externalValue: ./examples/profile-scored.json
                scored_via_fallback:
                  externalValue: ./examples/profile-scored-via-fallback.json
                unassigned:
                  externalValue: ./examples/profile-unassigned.json
                pending:
                  externalValue: ./examples/profile-pending.json
            application/xml:
              schema:
                $ref: '#/components/schemas/RfmProfile'
              examples:
                scored:
                  externalValue: ./examples/profile-scored.xml
                scored_via_fallback:
                  externalValue: ./examples/profile-scored-via-fa
… (60448 more characters)
```

<a id="exchange-2"></a>
## 2. GET /api/segments/openapi.yaml (version)

`X-Correlation-ID: f29-04-6a950c44c62a4268`, at 2026-11-24T03:57:11Z

```http
GET https://mosaiq.maxthecoder.online/api/segments/openapi.yaml
X-Correlation-ID: f29-04-6a950c44c62a4268


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f29-04-6a950c44c62a4268

openapi: 3.1.0
info:
  title: segments-service
  version: 1.6.2
  description: >-
    Serves what the segmentation runs recorded: the label vocabulary, each
    customer's current assignment and the history, the assignments of each model
    version, and the migrations between two instants with their matrix, entries,
    exits, cell members and per-customer explanation. A comparison across model
    versions is marked methodological and is never behaviour (ADR-0064). It writes
    nothing through HTTP; assignments are applied through
    `api_segments.apply_assignment_v1` (ADR-0063) (RN-20, RN-21, RN-39, RN-59,
    RN-60).
security:
  - bearer: []
tags:
  - name: Segments
  - name: Assignments
  - name: Migrations
  - name: Meta
paths:
  /v1/segments:
    get:
      tags: [Segments]
      summary: List the label vocabulary
      description: >-
        Returns every label in business order (`ordinal_position`), each with its
        size in the latest run. The list also carries that run's `run_id` and
        `run_at` and the number of customers it left unassigned. Before any run,
        `run_id` and `run_at` are null (omitted in XML), every size is 0 and the
        unassigned count is 0. A segment is named by its stable label code, never by
        an id that belongs to one run. Cache misses read PostgreSQL, so a database
        failure stays visible.
      operationId: listSegments
      x-mosaiq-roles:
        - ADMIN
        - ANALYST
        - STORE_MANAGER
        - MARKETING
        - AUDITOR
        - SALES_ASSOCIATE
        - LOYALTY_OFFICER
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
      responses:
        '200':
          description: The labels.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/SegmentList'
              examples:
                latest_run:
                  externalValue: ./examples/list-segments.json
                before_first_run:
                  externalValue: ./examples/list-segments-before-first-run.json
            application/xml:
              schema:
                $ref: '#/components/schemas/SegmentList'
              examples:
                latest_run:
                  externalValue: ./examples/list-segments.xml
                before_first_run:
                  externalValue: ./examples/list-segments-before-first-run.xml
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '429':
          $ref: '#/components/responses/RateLimited'
        '503':
          $ref: '#/components/responses/OperationUnavailable'
  /v1/segments/{label_code}:
    get:
      tags: [Segments]
      summary: Get one label
      description: >-
        Returns one label with its size in the latest run. A segment is named by its
        stable label code, never by an id that belongs to one run.
      operationId: getSegment
      x-mosaiq-roles:
        - ADMIN
        - ANALYST
        - STORE_MANAGER
        - MARKETING
        - AUDITOR
        - SALES_ASSOCIATE
        - LOYALTY_OFFICER
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
        - $ref: '#/components/parameters/LabelCode'
      responses:
        '200':
          description: The requested label.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Segment'
              examples:
                segment:
                  externalValue: ./examples/get-segment.json
            application/xml:
              schema:
                $ref: '#/components/schemas/Segment'
              examples:
                segment:
                  externalValue: ./examples/get-segment.xml
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '404':
          $ref: '#/components/responses/SegmentNotFound'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '429':
          $ref: '#/components/responses/RateLimited'
        '503':
          $ref: '#/components/responses/OperationUnavailable'
  /v1/segment-assignments/current:
    get:
      tags: [Segments]
      summary: Get a customer's current assignment
      description: >-
        Returns the customer's current state, which is always a 200 answer.
        `assigned` carries the label, the three scores, `since` (the open row's
        `valid_from`) and `run_id`. `unassigned_result` means the latest run left
        the customer without a label and carries `since` and `run_id` only.
        `no_open_assignment` means the customer has no open row and carries nothing
        more. The last two are answers, not errors, and they are never filled in
        from another source. A customer absent from `api_customers.customer_ref_v1`
        is 404 `customer_not_found`; a missing or malformed `customer_id` is 422
        `invalid_query`.
      operationId: getCurrentAssignment
      x-mosaiq-roles:
        - ADMIN
        - ANALYST
        - STORE_MANAGER
        - MARKETING
        - AUDITOR
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
        - $ref: '#/components/parameters/CurrentCustomerId'
      responses:
        '200':
          description: The customer's current state.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/CurrentAssignment'
              examples:
                assigned:
              
… (61204 more characters)
```

<a id="exchange-3"></a>
## 3. GET /api/model-registry/openapi.yaml (version)

`X-Correlation-ID: f29-04-16a750f30be843b4`, at 2026-11-24T03:57:11Z

```http
GET https://mosaiq.maxthecoder.online/api/model-registry/openapi.yaml
X-Correlation-ID: f29-04-16a750f30be843b4


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f29-04-16a750f30be843b4

openapi: 3.1.0
info:
  title: model-registry-service
  version: 1.3.0
  description: >-
    Keeps every model version immutable and says which one is published. A model version is
    immutable once published, carries its dataset, parameters, metrics and reference distribution,
    and changes state only by an explicit publication or rollback (RN-66, ADR-0064). model-registry
    serves `model-versions`, with their append-only publications and rollbacks beneath them,
    and `model-trainings`, the runs that fit and register draft versions. Exactly one version
    is published at a time, because a customer holds one current segment (RN-20). The current
    published version is published to other services as `api_models.published_version_v1`,
    not through this API (ADR-0044). It also serves `drift-reports` and `drift-alerts`, the
    other half of the rule: every report and alert is judged against the reference distribution
    of the version it names, and carries the statistic, the reference, the current window, the
    threshold and the model version (RN-66, ADR-0065). A change of version inside a window is
    never compared (ADR-0064). The drift operations (F23-09) are published ahead of their
    implementation (ADR-0073) and are marked planned.
security:
- bearer: []
tags:
- name: Versions
- name: Publications
- name: Trainings
- name: Drift reports
- name: Drift alerts
- name: Meta
paths:
  /v1/model-versions:
    get:
      tags:
      - Versions
      summary: List model versions
      description: >-
        Returns a page of versions, newest first. `method` and `state` filter on the stored
        values. `state=PUBLISHED` is the version every customer is currently scored under:
        at most one row, because exactly one version is published at a time (ADR-0064). Each
        version carries its dataset, parameters, seed, metrics, owner, state and reference
        distribution.
      operationId: listModelVersions
      x-mosaiq-roles:
      - ADMIN
      - ANALYST
      - MARKETING
      - AUDITOR
      parameters:
      - $ref: '#/components/parameters/CorrelationId'
      - $ref: '#/components/parameters/MethodFilter'
      - $ref: '#/components/parameters/VersionStateFilter'
      - $ref: '#/components/parameters/Page'
      - $ref: '#/components/parameters/Size'
      responses:
        '200':
          description: A page of versions.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ModelVersionPage'
              examples:
                page:
                  externalValue: ./examples/model-versions-page.json
                none:
                  externalValue: ./examples/model-versions-none.json
            application/xml:
              schema:
                $ref: '#/components/schemas/ModelVersionPage'
              examples:
                page:
                  externalValue: ./examples/model-versions-page.xml
                none:
                  externalValue: ./examples/model-versions-none.xml
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '422':
          $ref: '#/components/responses/ListInvalid'
        '429':
          $ref: '#/components/responses/RateLimited'
        '503':
          $ref: '#/components/responses/OperationUnavailable'
    post:
      tags:
      - Versions
      summary: Register a draft version
      description: >-
        Registers an immutable DRAFT version from a definition fitted elsewhere, such as an
        RFM rule set (`method` RFM_RULES, `parameters.rule_set_id` naming the rfm-engine rule
        set). Registering publishes nothing. There is no operation to edit or delete a version,
        published or not: a change is a new version, and the absent routes answer 405 `method_not_allowed`.
        A version trained by the registry is created by `POST /v1/model-trainings`, not here.
        `parameters`, `seed` and `frozen_scoring` must belong to `method`, and boundaries
        must be monotonic, or the answer is 422 `invalid_body` with a detail code `method_mismatch`,
        `not_monotonic` or `distribution_mismatch`. A name already used is 409 `model_version_name_taken`.
      operationId: createModelVersion
      x-mosaiq-roles:
      - ADMIN
      parameters:
      - $ref: '#/components/parameters/CorrelationId'
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ModelVersionRequest'
            examples:
              rfm_rules:
                externalValue: ./examples/model-version.request.json
              kmeans:
                externalValue: ./examples/model-version-kmeans.request.json
          application/xml:
            schema:
              $ref: '#/components/schemas/ModelVersionRequest'
            examples:
              rfm_rules:
                externalValue: ./examples/model-version.request.xml
              kmeans:
                externalValue: ./examples/model-version-kmeans.request.xml
      responses:
        '201':
          description: The draft version was registered.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ModelVersion'
              examples:
                rfm_rules:
                  externalValue: ./examples/model-version-created.json
            application/xml:
              schema:
                $ref: '#/components/schemas/ModelVersion'
              examples:
                rfm_rules:
                  externalValue: ./examples/model-version-created.xml
        '40
… (90018 more characters)
```

<a id="exchange-4"></a>
## 4. POST /api/auth/v1/sessions (sign in, ADMIN)

`X-Correlation-ID: f29-04-0fc3565d12584e2d`, at 2026-11-24T03:57:12Z

```http
POST https://mosaiq.maxthecoder.online/api/auth/v1/sessions
X-Correlation-ID: f29-04-0fc3565d12584e2d
Accept: application/json
Content-Type: application/json

{
  "email": "<administrator email, masked>",
  "password": "<masked #3>",
  "device_label": "F29-04 on the instance"
}
```

```http
HTTP 201
Content-Type: application/json
x-correlation-id: f29-04-0fc3565d12584e2d

{
  "access_token": "<masked #1>",
  "refresh_token": "<masked #2>",
  "token_type": "Bearer",
  "expires_in": 600,
  "expires_at": "2026-11-24T04:07:12Z"
}
```

<a id="exchange-5"></a>
## 5. POST /api/rfm-engine/v1/rfm-recalculations (start a verification)

`X-Correlation-ID: f29-04-38ccc3b4627e4f70`, at 2026-11-24T03:57:12Z

```http
POST https://mosaiq.maxthecoder.online/api/rfm-engine/v1/rfm-recalculations
X-Correlation-ID: f29-04-38ccc3b4627e4f70
Authorization: Bearer <masked #1>
Accept: application/json
Content-Type: application/json

{}
```

```http
HTTP 201
Content-Type: application/json
location: /v1/rfm-recalculations/1
x-correlation-id: f29-04-38ccc3b4627e4f70

{
  "recalculation_id": 1,
  "trigger": "ON_DEMAND",
  "state": "RUNNING",
  "rule_set_id": 1,
  "as_of": "2026-11-24T03:57:12.298994Z",
  "started_at": "2026-11-24T03:57:12.298994Z",
  "finished_at": null,
  "started_by": "494070dd-7cd3-4d3c-a29b-dd8a94f0d981",
  "customers_checked": null,
  "customers_different": null,
  "failure_code": null,
  "outcome": null
}
```

<a id="exchange-6"></a>
## 6. GET /api/rfm-engine/v1/rfm-recalculations/1 (poll)

`X-Correlation-ID: f29-04-378199280b5a4f4d`, at 2026-11-24T03:57:17Z

```http
GET https://mosaiq.maxthecoder.online/api/rfm-engine/v1/rfm-recalculations/1
X-Correlation-ID: f29-04-378199280b5a4f4d
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
x-correlation-id: f29-04-378199280b5a4f4d

{
  "recalculation_id": 1,
  "trigger": "ON_DEMAND",
  "state": "COMPLETED",
  "rule_set_id": 1,
  "as_of": "2026-11-24T03:57:12.298994Z",
  "started_at": "2026-11-24T03:57:12.298994Z",
  "finished_at": "2026-11-24T03:57:12.337083Z",
  "started_by": "494070dd-7cd3-4d3c-a29b-dd8a94f0d981",
  "customers_checked": 30,
  "customers_different": 0,
  "failure_code": null,
  "outcome": "EQUIVALENT"
}
```

<a id="exchange-7"></a>
## 7. GET /api/segments/v1/segment-migrations (the customer's migrations)

`X-Correlation-ID: f29-04-bf7fc36c61c24096`, at 2026-11-24T03:57:17Z

```http
GET https://mosaiq.maxthecoder.online/api/segments/v1/segment-migrations?from=2026-11-24T03%3A45%3A14Z&to=2026-11-24T03%3A57%3A17Z&customer_id=00000000-0000-0000-0000-000000000028
X-Correlation-ID: f29-04-bf7fc36c61c24096
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
x-correlation-id: f29-04-bf7fc36c61c24096

{
  "from": "2026-11-24T03:45:14Z",
  "to": "2026-11-24T03:57:17Z",
  "migrations": [
    {
      "customer_id": "00000000-0000-0000-0000-000000000028",
      "previous_state": "label",
      "previous_label_code": "LOYAL",
      "new_state": "label",
      "new_label_code": "CHAMPION",
      "movement": "improved",
      "changed_at": "2026-11-24T03:51:17.755230Z",
      "cause": "SALE",
      "previous_run_id": 31,
      "new_run_id": 41,
      "previous_model_version_id": 1,
      "new_model_version_id": null,
      "methodological": true,
      "responsible_variables": [
        "recency",
        "frequency"
      ],
      "rank_only": false
    }
  ],
  "page_info": {
    "page": 1,
    "size": 20,
    "total": 1
  }
}
```

<a id="exchange-8"></a>
## 8. DELETE /api/auth/v1/sessions/current (sign out, ADMIN)

`X-Correlation-ID: f29-04-5866468433034811`, at 2026-11-24T03:57:18Z

```http
DELETE https://mosaiq.maxthecoder.online/api/auth/v1/sessions/current
X-Correlation-ID: f29-04-5866468433034811
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 204
x-correlation-id: f29-04-5866468433034811


```
