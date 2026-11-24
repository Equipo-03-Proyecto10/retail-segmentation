# F29-05 Campaña A/B: requests and responses

<a id="exchange-1"></a>
## 1. POST /api/auth/v1/sessions (sign in, reader)

`X-Correlation-ID: f29-05-4d4f6787f30c4de0`, at 2026-11-24T03:53:13Z

```http
POST http://localhost:18129/api/auth/v1/sessions
X-Correlation-ID: f29-05-4d4f6787f30c4de0
Accept: application/json
Content-Type: application/json

{
  "email": "user2@mosaiq-demo.com",
  "password": "<masked #3>",
  "device_label": "F29-05 Campaña A/B"
}
```

```http
HTTP 201
Content-Type: application/json
X-Correlation-ID: f29-05-4d4f6787f30c4de0

{
  "access_token": "<masked #1>",
  "refresh_token": "<masked #2>",
  "token_type": "Bearer",
  "expires_in": 600,
  "expires_at": "2026-11-24T04:03:13Z"
}
```

<a id="exchange-2"></a>
## 2. POST /api/auth/v1/sessions (sign in, marketing)

`X-Correlation-ID: f29-05-8cef3f3239d846f0`, at 2026-11-24T03:53:13Z

```http
POST http://localhost:18129/api/auth/v1/sessions
X-Correlation-ID: f29-05-8cef3f3239d846f0
Accept: application/json
Content-Type: application/json

{
  "email": "user4@mosaiq-demo.com",
  "password": "<masked #3>",
  "device_label": "F29-05 Campaña A/B"
}
```

```http
HTTP 201
Content-Type: application/json
X-Correlation-ID: f29-05-8cef3f3239d846f0

{
  "access_token": "<masked #4>",
  "refresh_token": "<masked #5>",
  "token_type": "Bearer",
  "expires_in": 600,
  "expires_at": "2026-11-24T04:03:13Z"
}
```

<a id="exchange-3"></a>
## 3. GET /api/auth/openapi.yaml (version)

`X-Correlation-ID: f29-05-0463468231b146a3`, at 2026-11-24T03:53:13Z

```http
GET http://localhost:18129/api/auth/openapi.yaml
X-Correlation-ID: f29-05-0463468231b146a3


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f29-05-0463468231b146a3

openapi: 3.1.0
info:
  title: auth-service
  version: 1.1.0
  description: >-
    Authenticates active accounts, issues RS256 access tokens, rotates refresh
    tokens, and owns session liveness under RN-52. Sign-in and refresh read the
    users service's published credential view; current-session reads, sign-out,
    and JWKS publication do not read PostgreSQL.
servers:
  - url: /api/auth
security:
  - bearer: []
paths:
  /v1/sessions:
    post:
      operationId: createSession
      summary: Sign in and create a session
      security: []
      parameters:
        - $ref: "#/components/parameters/CorrelationId"
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/SignInRequest"
            examples:
              sign_in:
                externalValue: examples/sign-in.request.json
          application/xml:
            schema:
              $ref: "#/components/schemas/SignInRequest"
            examples:
              sign_in:
                externalValue: examples/sign-in.request.xml
      responses:
        "201":
          description: Session created.
          headers:
            X-Correlation-ID:
              $ref: "#/components/headers/CorrelationId"
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/TokenResult"
              examples:
                sign_in:
                  externalValue: examples/sign-in.json
            application/xml:
              schema:
                $ref: "#/components/schemas/TokenResult"
              examples:
                sign_in:
                  externalValue: examples/sign-in.xml
        "400":
          $ref: "#/components/responses/MalformedBody"
        "401":
          description: >-
            Invalid credentials: invalid_credentials. A wrong password, an
            unknown email, and an inactive account deliberately return this
            same body so account state is not disclosed.
          headers:
            X-Correlation-ID:
              $ref: "#/components/headers/CorrelationId"
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Error"
              examples:
                invalid_credentials:
                  externalValue: examples/invalid-credentials.json
            application/xml:
              schema:
                $ref: "#/components/schemas/Error"
              examples:
                invalid_credentials:
                  externalValue: examples/invalid-credentials.xml
        "406":
          $ref: "#/components/responses/NotAcceptable"
        "413":
          $ref: "#/components/responses/PayloadTooLarge"
        "415":
          $ref: "#/components/responses/UnsupportedMediaType"
        "422":
          $ref: "#/components/responses/InvalidBody"
        "429":
          description: "The sign-in request limit was exceeded: rate_limited."
          headers:
            X-Correlation-ID:
              $ref: "#/components/headers/CorrelationId"
            Retry-After:
              $ref: "#/components/headers/RetryAfter"
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Error"
              examples:
                rate_limited:
                  externalValue: examples/rate-limited.json
            application/xml:
              schema:
                $ref: "#/components/schemas/Error"
              examples:
                rate_limited:
                  externalValue: examples/rate-limited.xml
        "503":
          description: >-
            Session state cannot be reached (auth_unavailable), or PostgreSQL
            cannot provide the published credential view
            (database_unavailable).
          headers:
            X-Correlation-ID:
              $ref: "#/components/headers/CorrelationId"
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Error"
              examples:
                auth_unavailable:
                  externalValue: examples/auth-unavailable.json
                database_unavailable:
                  externalValue: examples/database-unavailable.json
            application/xml:
              schema:
                $ref: "#/components/schemas/Error"
              examples:
                auth_unavailable:
                  externalValue: examples/auth-unavailable.xml
                database_unavailable:
                  externalValue: examples/database-unavailable.xml
  /v1/sessions/refresh:
    post:
      operationId: refreshSession
      summary: Rotate a refresh token
      description: >-
        Returns new access and refresh tokens without extending the session's
        absolute end.
      security: []
      parameters:
        - $ref: "#/components/parameters/CorrelationId"
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/RefreshRequest"
            examples:
              refresh:
                externalValue: examples/refresh.request.json
          application/xml:
            schema:
              $ref: "#/components/schemas/RefreshRequest"
            examples:
              refresh:
                externalValue: examples/refresh.request.xml
      responses:
        "200":
          description: Refresh token rotated.
          headers:
            X-Correlation-ID:
              $ref: "#/components/headers/CorrelationId"
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/TokenResult"
              examples:
                refresh:
                  externalValue: examples/refresh.json
            application/xml:
              schema:
                $ref: "#/components/schemas/TokenResult"
              examples:
           
… (17226 more characters)
```

<a id="exchange-4"></a>
## 4. GET /api/experimentation/openapi.yaml (version)

`X-Correlation-ID: f29-05-08e2af6f0f7e4ef4`, at 2026-11-24T03:53:13Z

```http
GET http://localhost:18129/api/experimentation/openapi.yaml
X-Correlation-ID: f29-05-08e2af6f0f7e4ef4


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f29-05-08e2af6f0f7e4ef4

openapi: 3.1.0
info:
  title: experimentation-service
  version: 1.2.1
  description: >-
    Plans experiments, assigns customers to their arms, evaluates conversions, credits sales to campaigns
    and reports results (RN-67). An experiment's frame and arms are fixed before measurement, assignment,
    exposure and conversion are separate append-only facts, a sale is attributed at most once, and an
    effect is reported only against a control, with its uncertainty (ADR-0059, ADR-0068, ADR-0069). experimentation
    serves `experiments`, with their conversions beneath them, `experiment-assignments`, `experiment-results`
    and `sale-attributions`.



    **A plan is frozen before anyone is assigned (ADR-0068).** `POST /v1/experiments` takes the frame
    at one recorded instant, estimates the baseline from it, computes the customers each arm needs, draws
    the salt and stores all of it immutably in one transaction. `POST /v1/experiment-assignments` then
    assigns the whole frame once, as a pure function of that plan: a customer's key is an HMAC of the
    published salt, so a third party can recompute every arm. An experiment whose frame cannot supply
    the customers per arm is flagged UNDERPOWERED and is assigned only on an explicit, recorded acknowledgement.
    An experiment is read once, at its analysis instant: before it a result shows counts and the elapsed
    share of the window and no interval, p-value or direction.



    **Effect needs a control.** No field of any representation states an effect, an incremental amount
    or an uplift for an experiment without a control. Such an experiment (`design_kind` OFFER_COMPARISON)
    compares two offers: its result states a `TREATMENT_DIFFERENCE` with its interval and an `effect_refusal`,
    never an effect against no treatment. A non-significant result of an underpowered experiment is INCONCLUSIVE,
    never "no effect".



    **Attribution and effect are two measurements, named apart (ADR-0069).** Attribution is commercial:
    which campaign a sale is credited to, counted once. It is read as `sale-attributions` and as the `attribution`
    section of a result, every figure labelled ATTRIBUTION, and none of its fields says incremental, uplift
    or caused. An effect is causal: a treatment's difference against the control over every assigned customer,
    with its interval, read as the `comparisons` of a result labelled EFFECT. Revenue of a treatment arm
    and attributed revenue are not effects.



    **Net, and as of a time.** Conversions and revenue are read net of reversals (ADR-0062). A conversion
    row stays when its sale is reversed and stops counting. Every figure that depends on net sales carries
    `as_of`. The primary result is stored once at its analysis instant and is never rewritten; a later
    read (`basis=RE_READ`) is labelled as of its own time.



    **A comparison needs enough customers in both arms.** A proportion comparison needs at least one
    customer per arm, and a Welch comparison needs at least two. A comparison that does not meet its
    minimum is omitted, and one `COMPARISON_WITHHELD_SMALL_ARM` assumption names its metric, arms and
    reason. Other comparisons in the same result remain present.



    **Exposure is not an endpoint of this service.** A real exposure is a `CAMPAIGN_EXPOSURE` of engagement,
    which reaches experimentation on the Redis Stream `stream:engagement.v1.interaction_recorded` into
    its own projection (ADR-0075). The projection only counts: `exposure_count` of an assignment and `exposed`
    of an arm. This service never calls engagement and never re-serves its events. A control is never
    exposed (ADR-0027).



    **The floor reads one customer.** `GET /v1/experiment-assignments/current` answers for one customer
    and one campaign, so a seller never reads the whole assignment. Reading the assignment of an experiment,
    which names every customer, is for the roles that read campaigns.



    **Not yet implemented.** An operation still marked `x-mosaiq-status: planned` (ADR-0073) is not served: the
    contract merges first (ADR-0035), and the implementing story removes the marker. The experiment tables the
    web writes are taken over by the stand-up story; this contract moves none of them.
security:
- bearer: []
tags:
- name: Experiments
- name: Assignments
- name: Conversions
- name: Results
- name: Attributions
- name: Meta
paths:
  /v1/experiments:
    get:
      tags:
      - Experiments
      summary: List experiments
      description: >-
        Returns a page of experiments, newest first by `experiment_id`. `campaign_id`, `state` and `data_origin`
        filter on the stored values. Each experiment carries its arms, design values, frame summary, assignment
        summary and flags, and never a customer.
      operationId: listExperiments
      x-mosaiq-roles:
      - ADMIN
      - ANALYST
      - MARKETING
      - AUDITOR
      parameters:
      - $ref: '#/components/parameters/CorrelationId'
      - $ref: '#/components/parameters/CampaignIdFilter'
      - $ref: '#/components/parameters/ExperimentStateFilter'
      - $ref: '#/components/parameters/DataOriginFilter'
      - $ref: '#/components/parameters/Page'
      - $ref: '#/components/parameters/Size'
      responses:
        '200':
          description: A page of experiments.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ExperimentPage'
              examples:
                page:
                  externalValue: ./examples/experiments-page.json
                none:
                  externalValue: ./examples/experiments-none.json
            application/xml:
              schema:
                $ref: '#/components/schemas/ExperimentPage'
              examples:
                page:
                  externalV
… (100181 more characters)
```

<a id="exchange-5"></a>
## 5. GET /api/experimentation/v1/experiments/16 (the experiment)

`X-Correlation-ID: f29-05-ba53ddc4156741b4`, at 2026-11-24T03:53:13Z

```http
GET http://localhost:18129/api/experimentation/v1/experiments/16
X-Correlation-ID: f29-05-ba53ddc4156741b4
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
X-Correlation-ID: f29-05-ba53ddc4156741b4

{
  "experiment_id": 16,
  "name": "Experiment 16",
  "campaign_id": 16,
  "state": "ASSIGNED",
  "design_kind": "CONTROLLED",
  "assignment_rule": "V0",
  "data_origin": "SEEDED",
  "arms": [
    {
      "arm_id": 31,
      "position": 0,
      "kind": "CONTROL",
      "name": "Holdout 16",
      "treatment_description": "No treatment delivered."
    },
    {
      "arm_id": 32,
      "position": 1,
      "kind": "TREATMENT",
      "name": "Offer 16",
      "treatment_description": "Personalized offer for experiment 16"
    }
  ],
  "design": {
    "primary_metric": "CONVERSION",
    "secondary_metric": null,
    "estimator": null,
    "alpha": null,
    "comparison_alpha": null,
    "power": null,
    "minimum_detectable_effect": null,
    "baseline": null,
    "baseline_std_dev": null,
    "required_per_arm": null,
    "smallest_detectable_effect": null,
    "allocation": null,
    "conversion_window_days": 28,
    "settle_days": null,
    "starts_on": "2026-07-08",
    "analysis_at": null
  },
  "frame": null,
  "salt": null,
  "assignment": {
    "experiment_id": 16,
    "assigned_at": "2026-07-09T03:40:24.930944Z",
    "assigned_by": null,
    "assigned_total": 4,
    "acknowledged_underpowered": false,
    "arms": [
      {
        "arm_id": 31,
        "kind": "CONTROL",
        "assigned": 2
      },
      {
        "arm_id": 32,
        "kind": "TREATMENT",
        "assigned": 2
      }
    ],
    "checks": null
  },
  "flags": [],
  "planned_at": null,
  "planned_by": null
}
```

<a id="exchange-6"></a>
## 6. GET /api/experimentation/v1/experiment-assignments (assignments before, page 1)

`X-Correlation-ID: f29-05-885cad82f41e4468`, at 2026-11-24T03:53:13Z

```http
GET http://localhost:18129/api/experimentation/v1/experiment-assignments?experiment_id=16&size=100&page=1
X-Correlation-ID: f29-05-885cad82f41e4468
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
X-Correlation-ID: f29-05-885cad82f41e4468

{
  "assignments": [
    {
      "assignment_id": 61,
      "experiment_id": 16,
      "campaign_id": 16,
      "customer_id": "00000000-0000-0000-0000-000000000001",
      "arm_id": 31,
      "arm_kind": "CONTROL",
      "arm_name": "Holdout 16",
      "assignment_key": null,
      "assigned_at": "2026-07-09T03:40:24.930944Z",
      "exposure_count": 0,
      "first_exposed_at": null
    },
    {
      "assignment_id": 62,
      "experiment_id": 16,
      "campaign_id": 16,
      "customer_id": "00000000-0000-0000-0000-000000000002",
      "arm_id": 31,
      "arm_kind": "CONTROL",
      "arm_name": "Holdout 16",
      "assignment_key": null,
      "assigned_at": "2026-07-12T08:40:24.930944Z",
      "exposure_count": 0,
      "first_exposed_at": null
    },
    {
      "assignment_id": 63,
      "experiment_id": 16,
      "campaign_id": 16,
      "customer_id": "00000000-0000-0000-0000-000000000003",
      "arm_id": 32,
      "arm_kind": "TREATMENT",
      "arm_name": "Offer 16",
      "assignment_key": null,
      "assigned_at": "2026-07-15T05:40:24.930944Z",
      "exposure_count": 1,
      "first_exposed_at": "2026-07-15T11:40:24.930944Z"
    },
    {
      "assignment_id": 64,
      "experiment_id": 16,
      "campaign_id": 16,
      "customer_id": "00000000-0000-0000-0000-000000000004",
      "arm_id": 32,
      "arm_kind": "TREATMENT",
      "arm_name": "Offer 16",
      "assignment_key": null,
      "assigned_at": "2026-07-18T10:40:24.930944Z",
      "exposure_count": 1,
      "first_exposed_at": "2026-07-18T14:40:24.930944Z"
    }
  ],
  "page_info": {
    "page": 1,
    "size": 100,
    "total": 4
  }
}
```

<a id="exchange-7"></a>
## 7. POST /api/experimentation/v1/experiment-assignments (assign the experiment again)

`X-Correlation-ID: f29-05-22bd632b3bd94160`, at 2026-11-24T03:53:13Z

```http
POST http://localhost:18129/api/experimentation/v1/experiment-assignments
X-Correlation-ID: f29-05-22bd632b3bd94160
Authorization: Bearer <masked #4>
Accept: application/json
Content-Type: application/json

{
  "experiment_id": 16
}
```

```http
HTTP 409
Content-Type: application/json
X-Correlation-ID: f29-05-22bd632b3bd94160

{
  "code": "already_assigned",
  "message": "The experiment is already assigned; an assignment is written once and never rewritten.",
  "correlation_id": "f29-05-22bd632b3bd94160",
  "details": []
}
```

<a id="exchange-8"></a>
## 8. PATCH /api/experimentation/v1/experiments/16 (patch the experiment)

`X-Correlation-ID: f29-05-a7648870a0574e22`, at 2026-11-24T03:53:13Z

```http
PATCH http://localhost:18129/api/experimentation/v1/experiments/16
X-Correlation-ID: f29-05-a7648870a0574e22
Authorization: Bearer <masked #4>
Accept: application/json
Content-Type: application/json

{
  "name": "renamed"
}
```

```http
HTTP 405
Content-Type: application/json
X-Correlation-ID: f29-05-a7648870a0574e22

{
  "code": "method_not_allowed",
  "message": "The method is not allowed for the requested URL.",
  "correlation_id": "f29-05-a7648870a0574e22",
  "details": []
}
```

<a id="exchange-9"></a>
## 9. DELETE /api/experimentation/v1/experiments/16 (delete the experiment)

`X-Correlation-ID: f29-05-4f5aec3a8201470a`, at 2026-11-24T03:53:13Z

```http
DELETE http://localhost:18129/api/experimentation/v1/experiments/16
X-Correlation-ID: f29-05-4f5aec3a8201470a
Authorization: Bearer <masked #4>
Accept: application/json


```

```http
HTTP 405
Content-Type: application/json
X-Correlation-ID: f29-05-4f5aec3a8201470a

{
  "code": "method_not_allowed",
  "message": "The method is not allowed for the requested URL.",
  "correlation_id": "f29-05-4f5aec3a8201470a",
  "details": []
}
```

<a id="exchange-10"></a>
## 10. PATCH /api/experimentation/v1/experiment-assignments/61 (patch an assignment)

`X-Correlation-ID: f29-05-4dcc476716d54393`, at 2026-11-24T03:53:13Z

```http
PATCH http://localhost:18129/api/experimentation/v1/experiment-assignments/61
X-Correlation-ID: f29-05-4dcc476716d54393
Authorization: Bearer <masked #4>
Accept: application/json
Content-Type: application/json

{
  "arm_id": 0
}
```

```http
HTTP 404
Content-Type: application/json
X-Correlation-ID: f29-05-4dcc476716d54393

{
  "code": "not_found",
  "message": "The requested URL was not found on the server. If you entered the URL manually please check your spelling and try again.",
  "correlation_id": "f29-05-4dcc476716d54393",
  "details": []
}
```

<a id="exchange-11"></a>
## 11. DELETE /api/experimentation/v1/experiment-assignments/61 (delete an assignment)

`X-Correlation-ID: f29-05-6c94ef4ac61c4d47`, at 2026-11-24T03:53:13Z

```http
DELETE http://localhost:18129/api/experimentation/v1/experiment-assignments/61
X-Correlation-ID: f29-05-6c94ef4ac61c4d47
Authorization: Bearer <masked #4>
Accept: application/json


```

```http
HTTP 404
Content-Type: application/json
X-Correlation-ID: f29-05-6c94ef4ac61c4d47

{
  "code": "not_found",
  "message": "The requested URL was not found on the server. If you entered the URL manually please check your spelling and try again.",
  "correlation_id": "f29-05-6c94ef4ac61c4d47",
  "details": []
}
```

<a id="exchange-12"></a>
## 12. GET /api/experimentation/v1/experiment-assignments (assignments after, page 1)

`X-Correlation-ID: f29-05-eee3dfff08d84bc6`, at 2026-11-24T03:53:13Z

```http
GET http://localhost:18129/api/experimentation/v1/experiment-assignments?experiment_id=16&size=100&page=1
X-Correlation-ID: f29-05-eee3dfff08d84bc6
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
X-Correlation-ID: f29-05-eee3dfff08d84bc6

{
  "assignments": [
    {
      "assignment_id": 61,
      "experiment_id": 16,
      "campaign_id": 16,
      "customer_id": "00000000-0000-0000-0000-000000000001",
      "arm_id": 31,
      "arm_kind": "CONTROL",
      "arm_name": "Holdout 16",
      "assignment_key": null,
      "assigned_at": "2026-07-09T03:40:24.930944Z",
      "exposure_count": 0,
      "first_exposed_at": null
    },
    {
      "assignment_id": 62,
      "experiment_id": 16,
      "campaign_id": 16,
      "customer_id": "00000000-0000-0000-0000-000000000002",
      "arm_id": 31,
      "arm_kind": "CONTROL",
      "arm_name": "Holdout 16",
      "assignment_key": null,
      "assigned_at": "2026-07-12T08:40:24.930944Z",
      "exposure_count": 0,
      "first_exposed_at": null
    },
    {
      "assignment_id": 63,
      "experiment_id": 16,
      "campaign_id": 16,
      "customer_id": "00000000-0000-0000-0000-000000000003",
      "arm_id": 32,
      "arm_kind": "TREATMENT",
      "arm_name": "Offer 16",
      "assignment_key": null,
      "assigned_at": "2026-07-15T05:40:24.930944Z",
      "exposure_count": 1,
      "first_exposed_at": "2026-07-15T11:40:24.930944Z"
    },
    {
      "assignment_id": 64,
      "experiment_id": 16,
      "campaign_id": 16,
      "customer_id": "00000000-0000-0000-0000-000000000004",
      "arm_id": 32,
      "arm_kind": "TREATMENT",
      "arm_name": "Offer 16",
      "assignment_key": null,
      "assigned_at": "2026-07-18T10:40:24.930944Z",
      "exposure_count": 1,
      "first_exposed_at": "2026-07-18T14:40:24.930944Z"
    }
  ],
  "page_info": {
    "page": 1,
    "size": 100,
    "total": 4
  }
}
```

<a id="exchange-13"></a>
## 13. DELETE /api/auth/v1/sessions/current (sign out, marketing)

`X-Correlation-ID: f29-05-7e7781cf853e47d5`, at 2026-11-24T03:53:14Z

```http
DELETE http://localhost:18129/api/auth/v1/sessions/current
X-Correlation-ID: f29-05-7e7781cf853e47d5
Authorization: Bearer <masked #4>
Accept: application/json


```

```http
HTTP 204
X-Correlation-ID: f29-05-7e7781cf853e47d5


```

<a id="exchange-14"></a>
## 14. DELETE /api/auth/v1/sessions/current (sign out, reader)

`X-Correlation-ID: f29-05-e83fe7c8a241466d`, at 2026-11-24T03:53:15Z

```http
DELETE http://localhost:18129/api/auth/v1/sessions/current
X-Correlation-ID: f29-05-e83fe7c8a241466d
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 204
X-Correlation-ID: f29-05-e83fe7c8a241466d


```
