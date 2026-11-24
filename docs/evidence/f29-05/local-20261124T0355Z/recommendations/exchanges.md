# F29-05 Recomendaciones: requests and responses

<a id="exchange-1"></a>
## 1. POST /api/auth/v1/sessions (sign in, reader)

`X-Correlation-ID: f29-05-ffc155251c3c4581`, at 2026-11-24T03:55:45Z

```http
POST http://localhost:18129/api/auth/v1/sessions
X-Correlation-ID: f29-05-ffc155251c3c4581
Accept: application/json
Content-Type: application/json

{
  "email": "user2@mosaiq-demo.com",
  "password": "<masked #3>",
  "device_label": "F29-05 Recomendaciones"
}
```

```http
HTTP 201
Content-Type: application/json
X-Correlation-ID: f29-05-ffc155251c3c4581

{
  "access_token": "<masked #1>",
  "refresh_token": "<masked #2>",
  "token_type": "Bearer",
  "expires_in": 600,
  "expires_at": "2026-11-24T04:05:45Z"
}
```

<a id="exchange-2"></a>
## 2. GET /api/auth/openapi.yaml (version)

`X-Correlation-ID: f29-05-b733731e8e1848af`, at 2026-11-24T03:55:45Z

```http
GET http://localhost:18129/api/auth/openapi.yaml
X-Correlation-ID: f29-05-b733731e8e1848af


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f29-05-b733731e8e1848af

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
## 3. GET /api/stock/openapi.yaml (version)

`X-Correlation-ID: f29-05-c531c2cc05074c01`, at 2026-11-24T03:55:45Z

```http
GET http://localhost:18129/api/stock/openapi.yaml
X-Correlation-ID: f29-05-c531c2cc05074c01


```

```http
HTTP 404


```

<a id="exchange-4"></a>
## 4. GET /api/products/openapi.yaml (version)

`X-Correlation-ID: f29-05-2d272ab998c942fa`, at 2026-11-24T03:55:45Z

```http
GET http://localhost:18129/api/products/openapi.yaml
X-Correlation-ID: f29-05-2d272ab998c942fa


```

```http
HTTP 404


```

<a id="exchange-5"></a>
## 5. GET /api/recommendations/openapi.yaml (version)

`X-Correlation-ID: f29-05-2bf3041430924206`, at 2026-11-24T03:55:45Z

```http
GET http://localhost:18129/api/recommendations/openapi.yaml
X-Correlation-ID: f29-05-2bf3041430924206


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f29-05-2bf3041430924206

openapi: 3.1.0
info:
  title: recommendations-service
  version: 2.0.4
  description: >-
    Answers what to recommend to one customer, and why, or why nothing qualifies
    (RN-40, restated by ADR-0067).

    `/v1` is frozen: `GET /v1/recommendations` keeps its request, its response and
    its XSD (`recommendations.xsd`, namespace `.../xml/recommendations/v1`) exactly
    as 1.0.0 published them, judged at the customer's usual store, so every
    existing caller keeps working. It never carries a store or channel the caller
    named, an alternative, an `elsewhere` entry or a decision id, and an old client
    would show any of them as stock in this store. ADR-0035 makes an element added
    to an XML response type a breaking change, so the new behaviour ships as `/v2`
    beside `/v1`, with `recommendations.v2.xsd` (namespace
    `.../xml/recommendations/v2`, served at `/schema.v2.xsd`).

    `/v2` judges a recommendation at a reference store and an optional reference
    channel the caller may name, gives each item its score factors, its availability
    and its reasons, offers a marked alternative for a product that is unavailable,
    and gives each answer a decision id. The decision is recorded, and
    `recommendation-decisions` (a top-level resource of this service, `/v2` only)
    reads it back: what was offered, what was excluded and why. A `/v1` answer is
    recorded too, but carries no decision id.

    The service reads published `api_*` views only (E-07): the seven of 1.0.0
    (`api_products.product_ref_v1`, `api_categories.category_tree_v1`,
    `api_stock.stock_level_v1`, `api_customers.customer_ref_v1`,
    `api_preferences.interest_category_v1`, `api_sales.purchase_line_v1` and
    `api_segments.current_label_v1`) and, for `/v2`, `api_stock.availability_v1`,
    `api_products.product_relation_v1`, `api_stores.store_directory_v2`,
    `api_sales.channel_v1`, `api_users.store_assignment_v1` and
    `api_models.run_version_v1`. It keeps a result cache, and since ADR-0067 it
    records each decision in a schema it owns (ADR-0046). Staleness bound: a result may be
    served from cache for at most 120 s after the facts behind it change, and
    normally within the relay's 5 s plus the consumer's turn. Items carry ids and
    figures only: clients fetch names with `GET /v1/products?ids=`.
security:
- bearer: []
tags:
- name: Recommendations
- name: Meta
paths:
  /v1/recommendations:
    get:
      tags:
      - Recommendations
      summary: Get a customer's recommendations
      description: Recommends active products that are in stock at the customer's
        usual store, not already bought in the window, and that match at least one
        stated signal (segment peers, a preferred category, or purchase history),
        each with its reasons. When nothing qualifies, `status` says why and `items`
        is empty (RN-40). Items are ordered by the number of signals that match,
        then by segment buyers, then by the category's share of the customer's buying,
        then by product id. The status is `unassigned_result` or `no_open_assignment`
        when the customer has no segment, and the segment is checked before the
        usual store.


        A customer whose `PERSONALIZATION` is not granted (an objection, a
        withdrawal, no decision recorded, or a consent view the service cannot
        read) is recommended nothing, so `status` is `none_match`, `items` is empty,
        `label_code`, `run_id` and `usual_store_id` are null, and no segment,
        purchase, interest or stock of the customer is read (ADR-0066, ADR-0067).
        `/v1` is frozen, so it cannot name the reason; `/v2` answers
        `PERSONALIZATION_WITHDRAWN`. A category the customer restricted, and every
        category below it, never appears. Consent and restrictions are read on
        every request, before the result cache, so a withdrawal is seen by the
        next read and the cache serves nothing older.
      operationId: getRecommendations
      x-mosaiq-roles:
      - ADMIN
      - ANALYST
      - STORE_MANAGER
      - MARKETING
      - AUDITOR
      parameters:
      - $ref: '#/components/parameters/CorrelationId'
      - $ref: '#/components/parameters/CustomerId'
      - $ref: '#/components/parameters/Limit'
      responses:
        '200':
          description: 'The result: items, or the status that says why there are
            none.'
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema: &id001
                $ref: '#/components/schemas/Recommendation'
              examples:
                recommended:
                  externalValue: ./examples/recommended.json
                none_match:
                  externalValue: ./examples/none-match.json
                no_open_assignment:
                  externalValue: ./examples/no-open-assignment.json
                unassigned_result:
                  externalValue: ./examples/unassigned-result.json
                no_usual_store:
                  externalValue: ./examples/no-usual-store.json
            application/xml:
              schema: *id001
              examples:
                recommended:
                  externalValue: ./examples/recommended.xml
                none_match:
                  externalValue: ./examples/none-match.xml
                no_open_assignment:
                  externalValue: ./examples/no-open-assignment.xml
                unassigned_result:
                  externalValue: ./examples/unassigned-result.xml
                no_usual_store:
                  externalValue: ./examples/no-usual-store.xml
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '422':
          $ref: '#/components/responses/Un
… (53760 more characters)
```

<a id="exchange-6"></a>
## 6. GET /api/recommendations/v2/recommendations (the read)

`X-Correlation-ID: f29-05-6d388ea2af3c457f`, at 2026-11-24T03:55:45Z

```http
GET http://localhost:18129/api/recommendations/v2/recommendations?customer_id=00000000-0000-0000-0000-000000000003&store_id=3
X-Correlation-ID: f29-05-6d388ea2af3c457f
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
X-Correlation-ID: f29-05-6d388ea2af3c457f

{
  "decision_id": "5449c5bd-7c7b-4c6f-9b1a-087f4a903bf2",
  "customer_id": "00000000-0000-0000-0000-000000000003",
  "status": "recommended",
  "computed_at": "2026-11-24T03:55:45.690832Z",
  "window": {
    "days": 180,
    "start": "2026-05-28T03:55:45.690832Z",
    "end": "2026-11-24T03:55:45.690832Z"
  },
  "label_code": "CHAMPION",
  "run_id": 31,
  "model_version_id": 1,
  "usual_store_id": 17,
  "store_id": 3,
  "store_source": "given",
  "channel_id": null,
  "exclusions": [
    {
      "reason": "already_bought",
      "count": 14
    },
    {
      "reason": "out_of_stock",
      "count": 21
    },
    {
      "reason": "PERSONALIZATION_WITHDRAWN",
      "count": 1
    }
  ],
  "items": [
    {
      "product_id": 2,
      "category_id": 2,
      "kind": "primary",
      "position": 1,
      "factors": {
        "signals_matched": 1,
        "segment_buyers": 3,
        "category_share": null
      },
      "availability": {
        "store_id": 3,
        "channel_id": null,
        "quantity_on_hand": 10
      },
      "alternative_for": null,
      "unavailable_reason": null,
      "reasons": [
        {
          "signal": "segment",
          "segment_buyers": 3,
          "category_id": null,
          "share": null
        }
      ]
    },
    {
      "product_id": 1,
      "category_id": 1,
      "kind": "primary",
      "position": 2,
      "factors": {
        "signals_matched": 1,
        "segment_buyers": 2,
        "category_share": null
      },
      "availability": {
        "store_id": 3,
        "channel_id": null,
        "quantity_on_hand": 34
      },
      "alternative_for": null,
      "unavailable_reason": null,
      "reasons": [
        {
          "signal": "segment",
          "segment_buyers": 2,
          "category_id": null,
          "share": null
        }
      ]
    },
    {
      "product_id": 3,
      "category_id": 3,
      "kind": "primary",
      "position": 3,
      "factors": {
        "signals_matched": 1,
        "segment_buyers": 2,
        "category_share": null
      },
      "availability": {
        "store_id": 3,
        "channel_id": null,
        "quantity_on_hand": 61
      },
      "alternative_for": null,
      "unavailable_reason": null,
      "reasons": [
        {
          "signal": "segment",
          "segment_buyers": 2,
          "category_id": null,
          "share": null
        }
      ]
    },
    {
      "product_id": 5,
      "category_id": 5,
      "kind": "primary",
      "position": 4,
      "factors": {
        "signals_matched": 1,
        "segment_buyers": 2,
        "category_share": null
      },
      "availability": {
        "store_id": 3,
        "channel_id": null,
        "quantity_on_hand": 18
      },
      "alternative_for": null,
      "unavailable_reason": null,
      "reasons": [
        {
          "signal": "segment",
          "segment_buyers": 2,
          "category_id": null,
          "share": null
        }
      ]
    }
  ]
}
```

<a id="exchange-7"></a>
## 7. DELETE /api/auth/v1/sessions/current (sign out, reader)

`X-Correlation-ID: f29-05-991c2d1be04c4767`, at 2026-11-24T03:55:46Z

```http
DELETE http://localhost:18129/api/auth/v1/sessions/current
X-Correlation-ID: f29-05-991c2d1be04c4767
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 204
X-Correlation-ID: f29-05-991c2d1be04c4767


```
