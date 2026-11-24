# F29-05 Exposición y conversión: requests and responses

<a id="exchange-1"></a>
## 1. POST /api/auth/v1/sessions (sign in, reader)

`X-Correlation-ID: f29-05-208ab1307a874e7d`, at 2026-11-24T03:53:15Z

```http
POST http://localhost:18129/api/auth/v1/sessions
X-Correlation-ID: f29-05-208ab1307a874e7d
Accept: application/json
Content-Type: application/json

{
  "email": "user2@mosaiq-demo.com",
  "password": "<masked #3>",
  "device_label": "F29-05 Exposición y conversión"
}
```

```http
HTTP 201
Content-Type: application/json
X-Correlation-ID: f29-05-208ab1307a874e7d

{
  "access_token": "<masked #1>",
  "refresh_token": "<masked #2>",
  "token_type": "Bearer",
  "expires_in": 600,
  "expires_at": "2026-11-24T04:03:15Z"
}
```

<a id="exchange-2"></a>
## 2. GET /api/auth/openapi.yaml (version)

`X-Correlation-ID: f29-05-b6838ee64f5846be`, at 2026-11-24T03:53:15Z

```http
GET http://localhost:18129/api/auth/openapi.yaml
X-Correlation-ID: f29-05-b6838ee64f5846be


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f29-05-b6838ee64f5846be

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

<a id="exchange-3"></a>
## 3. GET /api/experimentation/openapi.yaml (version)

`X-Correlation-ID: f29-05-3249cd6508094e29`, at 2026-11-24T03:53:15Z

```http
GET http://localhost:18129/api/experimentation/openapi.yaml
X-Correlation-ID: f29-05-3249cd6508094e29


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f29-05-3249cd6508094e29

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

<a id="exchange-4"></a>
## 4. GET /api/engagement/openapi.yaml (version)

`X-Correlation-ID: f29-05-2b7cc97950e44fee`, at 2026-11-24T03:53:15Z

```http
GET http://localhost:18129/api/engagement/openapi.yaml
X-Correlation-ID: f29-05-2b7cc97950e44fee


```

```http
HTTP 404


```

<a id="exchange-5"></a>
## 5. GET /api/experimentation/v1/experiment-assignments (the assignments, page 1)

`X-Correlation-ID: f29-05-046ae3fa63824266`, at 2026-11-24T03:53:15Z

```http
GET http://localhost:18129/api/experimentation/v1/experiment-assignments?experiment_id=16&size=100&page=1
X-Correlation-ID: f29-05-046ae3fa63824266
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
X-Correlation-ID: f29-05-046ae3fa63824266

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

<a id="exchange-6"></a>
## 6. GET /api/experimentation/v1/experiments/16/conversions (the conversions, page 1)

`X-Correlation-ID: f29-05-d51bb8b34663492f`, at 2026-11-24T03:53:15Z

```http
GET http://localhost:18129/api/experimentation/v1/experiments/16/conversions?size=100&page=1
X-Correlation-ID: f29-05-d51bb8b34663492f
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
X-Correlation-ID: f29-05-d51bb8b34663492f

{
  "conversions": [
    {
      "conversion_id": 51,
      "experiment_id": 16,
      "arm_id": 31,
      "customer_id": "00000000-0000-0000-0000-000000000001",
      "sale_id": 5,
      "sold_at": "2026-07-10T11:40:24.930944Z",
      "converted_at": "2026-07-10T11:40:24.930944Z",
      "sale_data_origin": "SEEDED",
      "counts": true,
      "net_amount": "283.75",
      "as_of": "2026-11-24T03:53:15.660120Z"
    },
    {
      "conversion_id": 52,
      "experiment_id": 16,
      "arm_id": 31,
      "customer_id": "00000000-0000-0000-0000-000000000001",
      "sale_id": 4,
      "sold_at": "2026-07-29T18:40:24.930944Z",
      "converted_at": "2026-07-29T18:40:24.930944Z",
      "sale_data_origin": "SEEDED",
      "counts": true,
      "net_amount": "326.25",
      "as_of": "2026-11-24T03:53:15.660120Z"
    },
    {
      "conversion_id": 53,
      "experiment_id": 16,
      "arm_id": 31,
      "customer_id": "00000000-0000-0000-0000-000000000002",
      "sale_id": 11,
      "sold_at": "2026-07-14T06:40:24.930944Z",
      "converted_at": "2026-07-14T06:40:24.930944Z",
      "sale_data_origin": "SEEDED",
      "counts": true,
      "net_amount": "296.25",
      "as_of": "2026-11-24T03:53:15.660120Z"
    },
    {
      "conversion_id": 54,
      "experiment_id": 16,
      "arm_id": 31,
      "customer_id": "00000000-0000-0000-0000-000000000002",
      "sale_id": 10,
      "sold_at": "2026-07-21T13:40:24.930944Z",
      "converted_at": "2026-07-21T13:40:24.930944Z",
      "sale_data_origin": "SEEDED",
      "counts": true,
      "net_amount": "353.25",
      "as_of": "2026-11-24T03:53:15.660120Z"
    },
    {
      "conversion_id": 55,
      "experiment_id": 16,
      "arm_id": 31,
      "customer_id": "00000000-0000-0000-0000-000000000002",
      "sale_id": 9,
      "sold_at": "2026-07-28T20:40:24.930944Z",
      "converted_at": "2026-07-28T20:40:24.930944Z",
      "sale_data_origin": "SEEDED",
      "counts": true,
      "net_amount": "197.25",
      "as_of": "2026-11-24T03:53:15.660120Z"
    },
    {
      "conversion_id": 56,
      "experiment_id": 16,
      "arm_id": 31,
      "customer_id": "00000000-0000-0000-0000-000000000002",
      "sale_id": 8,
      "sold_at": "2026-08-05T03:40:24.930944Z",
      "converted_at": "2026-08-05T03:40:24.930944Z",
      "sale_data_origin": "SEEDED",
      "counts": true,
      "net_amount": "233.25",
      "as_of": "2026-11-24T03:53:15.660120Z"
    },
    {
      "conversion_id": 57,
      "experiment_id": 16,
      "arm_id": 32,
      "customer_id": "00000000-0000-0000-0000-000000000003",
      "sale_id": 26,
      "sold_at": "2026-07-19T14:40:24.930944Z",
      "converted_at": "2026-07-19T14:40:24.930944Z",
      "sale_data_origin": "SEEDED",
      "counts": true,
      "net_amount": "592.50",
      "as_of": "2026-11-24T03:53:15.660120Z"
    },
    {
      "conversion_id": 58,
      "experiment_id": 16,
      "arm_id": 32,
      "customer_id": "00000000-0000-0000-0000-000000000003",
      "sale_id": 25,
      "sold_at": "2026-07-30T21:40:24.930944Z",
      "converted_at": "2026-07-30T21:40:24.930944Z",
      "sale_data_origin": "SEEDED",
      "counts": true,
      "net_amount": "706.50",
      "as_of": "2026-11-24T03:53:15.660120Z"
    },
    {
      "conversion_id": 59,
      "experiment_id": 16,
      "arm_id": 32,
      "customer_id": "00000000-0000-0000-0000-000000000003",
      "sale_id": 24,
      "sold_at": "2026-08-10T04:40:24.930944Z",
      "converted_at": "2026-08-10T04:40:24.930944Z",
      "sale_data_origin": "SEEDED",
      "counts": true,
      "net_amount": "394.50",
      "as_of": "2026-11-24T03:53:15.660120Z"
    },
    {
      "conversion_id": 60,
      "experiment_id": 16,
      "arm_id": 32,
      "customer_id": "00000000-0000-0000-0000-000000000004",
      "sale_id": 39,
      "sold_at": "2026-07-20T09:40:24.930944Z",
      "converted_at": "2026-07-20T09:40:24.930944Z",
      "sale_data_origin": "SEEDED",
      "counts": true,
      "net_amount": "263.00",
      "as_of": "2026-11-24T03:53:15.660120Z"
    },
    {
      "conversion_id": 61,
      "experiment_id": 16,
      "arm_id": 32,
      "customer_id": "00000000-0000-0000-0000-000000000004",
      "sale_id": 38,
      "sold_at": "2026-07-26T16:40:24.930944Z",
      "converted_at": "2026-07-26T16:40:24.930944Z",
      "sale_data_origin": "SEEDED",
      "counts": true,
      "net_amount": "311.00",
      "as_of": "2026-11-24T03:53:15.660120Z"
    },
    {
      "conversion_id": 62,
      "experiment_id": 16,
      "arm_id": 32,
      "customer_id": "00000000-0000-0000-0000-000000000004",
      "sale_id": 37,
      "sold_at": "2026-08-01T23:40:24.930944Z",
      "converted_at": "2026-08-01T23:40:24.930944Z",
      "sale_data_origin": "SEEDED",
      "counts": true,
      "net_amount": "366.00",
      "as_of": "2026-11-24T03:53:15.660120Z"
    },
    {
      "conversion_id": 63,
      "experiment_id": 16,
      "arm_id": 32,
      "customer_id": "00000000-0000-0000-0000-000000000004",
      "sale_id": 36,
      "sold_at": "2026-08-07T06:40:24.930944Z",
      "converted_at": "2026-08-07T06:40:24.930944Z",
      "sale_data_origin": "SEEDED",
      "counts": true,
      "net_amount": "200.00",
      "as_of": "2026-11-24T03:53:15.660120Z"
    },
    {
      "conversion_id": 64,
      "experiment_id": 16,
      "arm_id": 32,
      "customer_id": "00000000-0000-0000-0000-000000000004",
      "sale_id": 35,
      "sold_at": "2026-08-13T13:40:24.930944Z",
      "converted_at": "2026-08-13T13:40:24.930944Z",
      "sale_data_origin": "SEEDED",
      "counts": true,
      "net_amount": "227.00",
      "as_of": "2026-11-24T03:53:15.660120Z"
    }
  ],
  "page_info": {
    "page": 1,
    "size": 100,
    "total": 14
  }
}
```

<a id="exchange-7"></a>
## 7. GET /api/experimentation/v1/experiment-results/16 (the result)

`X-Correlation-ID: f29-05-0f69901a37224f96`, at 2026-11-24T03:53:15Z

```http
GET http://localhost:18129/api/experimentation/v1/experiment-results/16
X-Correlation-ID: f29-05-0f69901a37224f96
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
X-Correlation-ID: f29-05-0f69901a37224f96

{
  "experiment_id": 16,
  "campaign_id": 16,
  "basis": "PRIMARY",
  "status": "PENDING",
  "as_of": "2026-11-24T03:53:15.665869Z",
  "elapsed_share": 1.0,
  "data_origin": "SEEDED",
  "synthetic": true,
  "design_kind": "CONTROLLED",
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
  "arms": [
    {
      "arm_id": 31,
      "kind": "CONTROL",
      "name": "Holdout 16",
      "assigned": 2,
      "analysed": 2,
      "exposed": 0,
      "converted": 2,
      "pending": 0,
      "not_converted": 0,
      "conversion_rate": 1.0,
      "exposed_conversion_rate": null,
      "net_revenue": "1690.00",
      "revenue_per_customer": "845.00",
      "revenue_std_dev": 332.340187
    },
    {
      "arm_id": 32,
      "kind": "TREATMENT",
      "name": "Offer 16",
      "assigned": 2,
      "analysed": 2,
      "exposed": 2,
      "converted": 2,
      "pending": 0,
      "not_converted": 0,
      "conversion_rate": 1.0,
      "exposed_conversion_rate": 1.0,
      "net_revenue": "3060.50",
      "revenue_per_customer": "1530.25",
      "revenue_std_dev": 230.870364
    }
  ],
  "checks": null,
  "comparisons": [],
  "effect_refusal": null,
  "attribution": {
    "label": "ATTRIBUTION",
    "window_days": 28,
    "arms": [
      {
        "arm_id": 32,
        "attributed_sales": 8,
        "attributed_customers": 2,
        "attributed_revenue": "3060.50"
      }
    ],
    "attributed_sales": 8,
    "attributed_revenue": "3060.50",
    "unattributed_conversions": 0,
    "as_of": "2026-11-24T03:53:15.668913Z"
  },
  "assumptions": [
    {
      "code": "INTENT_TO_TREAT",
      "statement": "Every assigned customer counts in the arm they were assigned to, exposed or not."
    },
    {
      "code": "NET_OF_REVERSALS",
      "statement": "Conversions and revenue count sales net of reversals, as known at `as_of`."
    },
    {
      "code": "SINGLE_LOOK",
      "statement": "The result is read once, at the analysis instant; stopping early or adding a look needs a new experiment."
    }
  ],
  "flags": []
}
```

<a id="exchange-8"></a>
## 8. DELETE /api/auth/v1/sessions/current (sign out, reader)

`X-Correlation-ID: f29-05-c04cf646db89400a`, at 2026-11-24T03:53:16Z

```http
DELETE http://localhost:18129/api/auth/v1/sessions/current
X-Correlation-ID: f29-05-c04cf646db89400a
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 204
X-Correlation-ID: f29-05-c04cf646db89400a


```
