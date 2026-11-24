# F24-08 a stock change and the recommendation: requests and responses

<a id="exchange-1"></a>
## 1. POST /api/auth/v1/sessions (sign in, reader)

`X-Correlation-ID: f24-08-1d5f5a21a3554d0d`, at 2026-11-24T03:33:02Z

```http
POST https://127.0.0.1/api/auth/v1/sessions
X-Correlation-ID: f24-08-1d5f5a21a3554d0d
Accept: application/json
Content-Type: application/json

{
  "email": "s1-f2408-analyst@example.test",
  "password": "<masked #3>",
  "device_label": "F24-08 a stock change and the recommendation"
}
```

```http
HTTP 201
Content-Type: application/json
X-Correlation-ID: f24-08-1d5f5a21a3554d0d

{
  "access_token": "<masked #1>",
  "refresh_token": "<masked #2>",
  "token_type": "Bearer",
  "expires_in": 600,
  "expires_at": "2026-11-24T03:43:02Z"
}
```

<a id="exchange-2"></a>
## 2. POST /api/auth/v1/sessions (sign in, planner)

`X-Correlation-ID: f24-08-ddecc0938a934ba1`, at 2026-11-24T03:33:02Z

```http
POST https://127.0.0.1/api/auth/v1/sessions
X-Correlation-ID: f24-08-ddecc0938a934ba1
Accept: application/json
Content-Type: application/json

{
  "email": "s1-f2408-planner@example.test",
  "password": "<masked #6>",
  "device_label": "F24-08 a stock change and the recommendation"
}
```

```http
HTTP 201
Content-Type: application/json
X-Correlation-ID: f24-08-ddecc0938a934ba1

{
  "access_token": "<masked #4>",
  "refresh_token": "<masked #5>",
  "token_type": "Bearer",
  "expires_in": 600,
  "expires_at": "2026-11-24T03:43:02Z"
}
```

<a id="exchange-3"></a>
## 3. GET /api/auth/openapi.yaml (version)

`X-Correlation-ID: f24-08-babcffe268884bf7`, at 2026-11-24T03:33:02Z

```http
GET https://127.0.0.1/api/auth/openapi.yaml
X-Correlation-ID: f24-08-babcffe268884bf7


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f24-08-babcffe268884bf7

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
## 4. GET /api/stock/openapi.yaml (version)

`X-Correlation-ID: f24-08-229a3f933d044aba`, at 2026-11-24T03:33:02Z

```http
GET https://127.0.0.1/api/stock/openapi.yaml
X-Correlation-ID: f24-08-229a3f933d044aba


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f24-08-229a3f933d044aba

openapi: 3.1.0
info:
  title: stock-service
  version: 1.3.0
  description: 'Publishes the quantity held for each store and product pair, records the movements that
    change it, and states in which channels a product can be sold (RN-14, ADR-0061). A level, movement
    or availability carries ids and quantities only: no product name, price, store name or channel name,
    so clients fetch those from products, stores and sales by id (ADR-0035, no composition). A store holds
    one physical quantity per product; availability per channel is derived from it and is never a second
    quantity. Stock changes only through three doors: a sale (`api_stock.consume_v1`, called by sales
    inside its transaction), a sale''s reversal (`api_stock.restock_v1`, called by sales inside the reversal''s
    transaction), and a recorded movement posted here. Stock is never negative. Reservations are not adopted
    (ADR-0061). See contracts/db/api_stock.consume.v1.md, api_stock.restock.v1.md and api_stock.availability.v1.md.'
security:
- bearer: []
tags:
- name: Stock
- name: Meta
paths:
  /v1/stock-levels:
    get:
      tags:
      - Stock
      summary: List stock levels
      description: Returns a page of stock levels ordered by `store_id`, then `product_id`. `below=N`
        returns the levels whose `quantity_on_hand` is less than N. A level is read from PostgreSQL on
        every request and is never cached, so a phone sale and a CSV sale both show on the next read.
      operationId: listStockLevels
      x-mosaiq-roles: &id002
      - ADMIN
      - ANALYST
      - STORE_MANAGER
      - MARKETING
      - INVENTORY_PLANNER
      - AUDITOR
      - SALES_ASSOCIATE
      - LOYALTY_OFFICER
      - COMMERCIAL_SUPERVISOR
      parameters:
      - $ref: '#/components/parameters/CorrelationId'
      - $ref: '#/components/parameters/StoreId'
      - $ref: '#/components/parameters/ProductId'
      - $ref: '#/components/parameters/Below'
      - $ref: '#/components/parameters/Page'
      - $ref: '#/components/parameters/Size'
      responses:
        '200':
          description: A page of stock levels.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema: &id001
                $ref: '#/components/schemas/StockLevelPage'
              examples:
                filtered_page:
                  externalValue: ./examples/stock-levels-filtered.json
            application/xml:
              schema: *id001
              examples:
                filtered_page:
                  externalValue: ./examples/stock-levels-filtered.xml
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '422':
          $ref: '#/components/responses/InvalidQuery'
        '429':
          $ref: '#/components/responses/RateLimited'
        '503':
          $ref: '#/components/responses/OperationUnavailable'
  /v1/stock-levels/{store_id}/{product_id}:
    get:
      tags:
      - Stock
      summary: Get one stock level
      description: The store and product pair is the level's own identity (E-03). A level is read from
        PostgreSQL on every request and is never cached. A pair with no `inventory` row answers 404 `stock_level_not_found`.
      operationId: getStockLevel
      x-mosaiq-roles: *id002
      parameters:
      - $ref: '#/components/parameters/CorrelationId'
      - $ref: '#/components/parameters/StoreIdPath'
      - $ref: '#/components/parameters/ProductIdPath'
      responses:
        '200':
          description: The stock level.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema: &id003
                $ref: '#/components/schemas/StockLevel'
              examples:
                stock_level:
                  externalValue: ./examples/stock-level.json
            application/xml:
              schema: *id003
              examples:
                stock_level:
                  externalValue: ./examples/stock-level.xml
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '404':
          $ref: '#/components/responses/NotFound'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '429':
          $ref: '#/components/responses/RateLimited'
        '503':
          $ref: '#/components/responses/OperationUnavailable'
  /v1/stock-movements:
    post:
      tags:
      - Stock
      summary: Record a stock movement
      description: 'Records one receipt, transfer, count or adjustment and changes `inventory` in the
        same transaction. The movement is append-only: it is never edited or deleted, and a mistake is
        corrected by a later movement.


        | `movement_type` | Effect on on-hand | Body |

        |---|---|---|

        | `RECEIPT` | Adds `quantity` (positive) at `store_id`. Creates the stock record when the pair
        has none. | `store_id`, `product_id`, `quantity`, `reference` (for example a delivery note) |

        | `TRANSFER` | Two rows sharing a `transfer_id`, in one transaction: subtracts `quantity` (positive)
        at `origin_store_id`, adds it at `destination_store_id`. The destination record is created when
        absent. | `product_id`, `origin_store_id`, `destination_store_id`, `quantity` |

        | `COUNT` | Sets on-hand to `quantity` (zero or more) at `store_id`, and stores before and after.
        Creates the record when absent. | `store_id`, `product_id`, `quantity`, `counted_by` |

        | `ADJUSTMENT` | Adds `quantity_delta` (a non-zero integer, negative to subtract) at `store_id`.
        Needs an existing record. | `store_id`, `product_id`, `quantity_de
… (39538 more characters)
```

<a id="exchange-5"></a>
## 5. GET /api/products/openapi.yaml (version)

`X-Correlation-ID: f24-08-5fb60223483f49be`, at 2026-11-24T03:33:02Z

```http
GET https://127.0.0.1/api/products/openapi.yaml
X-Correlation-ID: f24-08-5fb60223483f49be


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f24-08-5fb60223483f49be

openapi: 3.1.0
info:
  title: products-service
  version: 1.4.0
  description: >-
    Owns the product catalog rule: a product has a unique SKU, a non-negative
    two-decimal list price, exactly one existing category, and is retired by
    deactivation. Reads may be cached, but a cache miss reads PostgreSQL. The
    batch `ids` filter lets clients resolve as many as 50 product references in
    one call; unknown ids are omitted rather than treated as errors.

    Commercial details (`/v1/products/{product_id}/details`) add a
    commercial status (`active`, `paused`, `discontinued`), attributes, a
    photo URL and a barcode. The `Product` representation of `listProducts`
    and `getProduct` is unchanged. `product.is_active` stays the one flag
    that the web, sales and recommendations read, and it is true exactly
    when the commercial status is `active`; the two are never stored or
    served out of step. Substitutes and complements of a product are under
    `/v1/products/{product_id}/relations` and are published to
    recommendations as `api_products.product_relation_v1`. Photos are
    uploaded through the web application, never through this service.
security:
  - bearer: []
paths:
  /v1/products:
    get:
      operationId: listProducts
      summary: List products
      description: >-
        Returns products ordered by `product_id`. Filters are combined. `q`
        matches a case-insensitive substring of the product name or SKU. `sku`
        is an exact match. `barcode` is an exact match on the product's
        barcode; barcodes are unique, so the page holds at most one product,
        and a barcode no product carries gives an empty page, not 404. `ids` is
        a comma-separated list of at most 50 positive product ids; ids with no
        matching product are omitted from the page and are not errors.
        Pagination applies after filtering. A filter value outside its schema
        returns 422 `invalid_query`, naming the parameter in `details`.
      x-mosaiq-roles:
        - ADMIN
        - ANALYST
        - STORE_MANAGER
        - MARKETING
        - INVENTORY_PLANNER
        - AUDITOR
        - SALES_ASSOCIATE
        - LOYALTY_OFFICER
        - COMMERCIAL_SUPERVISOR
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
        - name: q
          in: query
          description: Case-insensitive substring matched against name or SKU.
          schema:
            type: string
            minLength: 1
            maxLength: 150
        - name: sku
          in: query
          description: Exact SKU.
          schema:
            type: string
            minLength: 1
            maxLength: 40
        - name: barcode
          in: query
          description: >-
            Exact textual barcode, digits only, in the form printed on the
            label. Matching uses the digits as stored: a GTIN-12 does not match
            its zero-padded GTIN-13. The barcode itself is served by
            `getProductDetails`, not in the `Product` representation. A value
            outside the `Barcode` pattern returns 422 `invalid_query` with an
            `invalid_format` detail naming `barcode`; a well-formed value with
            a wrong check digit matches no product and gives an empty page.
          example: '4006381333931'
          schema:
            $ref: '#/components/schemas/Barcode'
        - name: category_id
          in: query
          description: Existing category identifier to filter by.
          schema:
            type: integer
            minimum: 1
            maximum: 32767
        - name: active
          in: query
          description: Whether to return active or inactive products.
          schema:
            type: boolean
        - name: ids
          in: query
          description: >-
            Comma-separated list of one to 50 positive product ids. Unknown ids
            are omitted. More than 50 ids returns 422 `too_many_ids`; the
            pattern below leaves the count to the service so that refusal
            carries its own code rather than a generic schema error.
          example: 1001,1002,1003
          schema:
            type: string
            pattern: '^[1-9][0-9]*(,[1-9][0-9]*)*$'
        - $ref: '#/components/parameters/Page'
        - $ref: '#/components/parameters/Size'
      responses:
        '200':
          description: A page of products.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Products'
              examples:
                products:
                  externalValue: ./examples/products.json
                fifty_id_batch:
                  externalValue: ./examples/products-50-id-batch.json
                by_barcode:
                  externalValue: ./examples/products-by-barcode.json
            application/xml:
              schema:
                $ref: '#/components/schemas/Products'
              examples:
                products:
                  externalValue: ./examples/products.xml
                fifty_id_batch:
                  externalValue: ./examples/products-50-id-batch.xml
                by_barcode:
                  externalValue: ./examples/products-by-barcode.xml
        '401':
          $ref: '#/components/responses/AuthenticationError'
        '403':
          $ref: '#/components/responses/ForbiddenRole'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '422':
          $ref: '#/components/responses/ProductListValidation'
        '429':
          $ref: '#/components/responses/RateLimited'
        '503':
          $ref: '#/components/responses/ProtectedDatabaseUnavailable'
    post:
      operationId: createProduct
      summary: Create a product
      description: >-
        Creates an active product with the caller-supplied `product_id`. The SKU
        
… (49459 more characters)
```

<a id="exchange-6"></a>
## 6. GET /api/recommendations/openapi.yaml (version)

`X-Correlation-ID: f24-08-1951e4178dc54087`, at 2026-11-24T03:33:02Z

```http
GET https://127.0.0.1/api/recommendations/openapi.yaml
X-Correlation-ID: f24-08-1951e4178dc54087


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f24-08-1951e4178dc54087

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

<a id="exchange-7"></a>
## 7. GET /api/recommendations/v2/recommendations (before)

`X-Correlation-ID: f24-08-b929945dabc041f2`, at 2026-11-24T03:33:03Z

```http
GET https://127.0.0.1/api/recommendations/v2/recommendations?customer_id=00000000-0000-0000-0000-000000000003&store_id=3
X-Correlation-ID: f24-08-b929945dabc041f2
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
X-Correlation-ID: f24-08-b929945dabc041f2

{
  "decision_id": "2a948a39-c77f-42f7-8576-13f57cfd7914",
  "customer_id": "00000000-0000-0000-0000-000000000003",
  "status": "recommended",
  "computed_at": "2026-11-24T03:31:43.621405Z",
  "window": {
    "days": 180,
    "start": "2026-05-28T03:31:43.621405Z",
    "end": "2026-11-24T03:31:43.621405Z"
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
      "product_id": 1,
      "category_id": 1,
      "kind": "primary",
      "position": 1,
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
      "product_id": 2,
      "category_id": 2,
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
        "quantity_on_hand": 10
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
        "segment_buyers": 1,
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
          "segment_buyers": 1,
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
        "segment_buyers": 1,
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
          "segment_buyers": 1,
          "category_id": null,
          "share": null
        }
      ]
    }
  ]
}
```

<a id="exchange-8"></a>
## 8. GET /api/recommendations/v2/recommendations (before, again (the cache))

`X-Correlation-ID: f24-08-4c849d928c7d4dbc`, at 2026-11-24T03:33:03Z

```http
GET https://127.0.0.1/api/recommendations/v2/recommendations?customer_id=00000000-0000-0000-0000-000000000003&store_id=3
X-Correlation-ID: f24-08-4c849d928c7d4dbc
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
X-Correlation-ID: f24-08-4c849d928c7d4dbc

{
  "decision_id": "2a948a39-c77f-42f7-8576-13f57cfd7914",
  "customer_id": "00000000-0000-0000-0000-000000000003",
  "status": "recommended",
  "computed_at": "2026-11-24T03:31:43.621405Z",
  "window": {
    "days": 180,
    "start": "2026-05-28T03:31:43.621405Z",
    "end": "2026-11-24T03:31:43.621405Z"
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
      "product_id": 1,
      "category_id": 1,
      "kind": "primary",
      "position": 1,
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
      "product_id": 2,
      "category_id": 2,
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
        "quantity_on_hand": 10
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
        "segment_buyers": 1,
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
          "segment_buyers": 1,
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
        "segment_buyers": 1,
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
          "segment_buyers": 1,
          "category_id": null,
          "share": null
        }
      ]
    }
  ]
}
```

<a id="exchange-9"></a>
## 9. GET /api/stock/v1/stock-levels/3/2 (stock level before)

`X-Correlation-ID: f24-08-3202e9304bb94342`, at 2026-11-24T03:33:03Z

```http
GET https://127.0.0.1/api/stock/v1/stock-levels/3/2
X-Correlation-ID: f24-08-3202e9304bb94342
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
x-correlation-id: f24-08-3202e9304bb94342

{
  "store_id": 3,
  "product_id": 2,
  "quantity_on_hand": 10,
  "updated_at": "2026-11-23T20:16:25.843243Z"
}
```

<a id="exchange-10"></a>
## 10. POST /api/stock/v1/stock-movements (COUNT to zero)

`X-Correlation-ID: f24-08-78296c3946a245b5`, at 2026-11-24T03:33:03Z

```http
POST https://127.0.0.1/api/stock/v1/stock-movements
X-Correlation-ID: f24-08-78296c3946a245b5
Idempotency-Key: 1c3a3be5-82b5-4310-af67-20cca0cab037
Authorization: Bearer <masked #4>
Accept: application/json
Content-Type: application/json

{
  "movement_type": "COUNT",
  "store_id": 3,
  "product_id": 2,
  "quantity": 0,
  "counted_by": "F24-08 acceptance (#831)",
  "note": "F24-08 (#831): leaves the product at zero"
}
```

```http
HTTP 201
Content-Type: application/json
x-correlation-id: f24-08-78296c3946a245b5

{
  "movements": [
    {
      "movement_id": 36,
      "movement_type": "COUNT",
      "store_id": 3,
      "product_id": 2,
      "quantity_delta": -10,
      "quantity_before": 10,
      "quantity_after": 0,
      "transfer_id": null,
      "reference": null,
      "reason": null,
      "counted_by": "F24-08 acceptance (#831)",
      "note": "F24-08 (#831): leaves the product at zero",
      "reversal_id": null,
      "recorded_by": "b914c065-b19e-4372-a920-9888f8e6963e",
      "recorded_at": "2026-11-24T03:33:03.157985Z"
    }
  ]
}
```

<a id="exchange-11"></a>
## 11. GET /api/recommendations/v2/recommendations (poll (1. Agotado))

`X-Correlation-ID: f24-08-07a4d5f2fb404bcf`, at 2026-11-24T03:33:03Z

```http
GET https://127.0.0.1/api/recommendations/v2/recommendations?customer_id=00000000-0000-0000-0000-000000000003&store_id=3
X-Correlation-ID: f24-08-07a4d5f2fb404bcf
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
X-Correlation-ID: f24-08-07a4d5f2fb404bcf

{
  "decision_id": "967eb947-3c6c-43aa-9204-edd2545b84bb",
  "customer_id": "00000000-0000-0000-0000-000000000003",
  "status": "recommended",
  "computed_at": "2026-11-24T03:33:03.226584Z",
  "window": {
    "days": 180,
    "start": "2026-05-28T03:33:03.226584Z",
    "end": "2026-11-24T03:33:03.226584Z"
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
      "count": 22
    },
    {
      "reason": "PERSONALIZATION_WITHDRAWN",
      "count": 1
    }
  ],
  "items": [
    {
      "product_id": 1,
      "category_id": 1,
      "kind": "primary",
      "position": 1,
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
      "position": 2,
      "factors": {
        "signals_matched": 1,
        "segment_buyers": 1,
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
          "segment_buyers": 1,
          "category_id": null,
          "share": null
        }
      ]
    },
    {
      "product_id": 5,
      "category_id": 5,
      "kind": "primary",
      "position": 3,
      "factors": {
        "signals_matched": 1,
        "segment_buyers": 1,
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
          "segment_buyers": 1,
          "category_id": null,
          "share": null
        }
      ]
    },
    {
      "product_id": 2,
      "category_id": 2,
      "kind": "elsewhere",
      "position": 4,
      "factors": {
        "signals_matched": 1,
        "segment_buyers": 2,
        "category_share": null
      },
      "availability": {
        "store_id": 13,
        "channel_id": null,
        "quantity_on_hand": 25
      },
      "alternative_for": null,
      "unavailable_reason": "out_of_stock",
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

<a id="exchange-12"></a>
## 12. GET /api/recommendations/v2/recommendations (read after the bound (1. Agotado))

`X-Correlation-ID: f24-08-25e66e8df5c14383`, at 2026-11-24T03:33:08Z

```http
GET https://127.0.0.1/api/recommendations/v2/recommendations?customer_id=00000000-0000-0000-0000-000000000003&store_id=3
X-Correlation-ID: f24-08-25e66e8df5c14383
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
X-Correlation-ID: f24-08-25e66e8df5c14383

{
  "decision_id": "967eb947-3c6c-43aa-9204-edd2545b84bb",
  "customer_id": "00000000-0000-0000-0000-000000000003",
  "status": "recommended",
  "computed_at": "2026-11-24T03:33:03.226584Z",
  "window": {
    "days": 180,
    "start": "2026-05-28T03:33:03.226584Z",
    "end": "2026-11-24T03:33:03.226584Z"
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
      "count": 22
    },
    {
      "reason": "PERSONALIZATION_WITHDRAWN",
      "count": 1
    }
  ],
  "items": [
    {
      "product_id": 1,
      "category_id": 1,
      "kind": "primary",
      "position": 1,
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
      "position": 2,
      "factors": {
        "signals_matched": 1,
        "segment_buyers": 1,
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
          "segment_buyers": 1,
          "category_id": null,
          "share": null
        }
      ]
    },
    {
      "product_id": 5,
      "category_id": 5,
      "kind": "primary",
      "position": 3,
      "factors": {
        "signals_matched": 1,
        "segment_buyers": 1,
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
          "segment_buyers": 1,
          "category_id": null,
          "share": null
        }
      ]
    },
    {
      "product_id": 2,
      "category_id": 2,
      "kind": "elsewhere",
      "position": 4,
      "factors": {
        "signals_matched": 1,
        "segment_buyers": 2,
        "category_share": null
      },
      "availability": {
        "store_id": 13,
        "channel_id": null,
        "quantity_on_hand": 25
      },
      "alternative_for": null,
      "unavailable_reason": "out_of_stock",
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

<a id="exchange-13"></a>
## 13. GET /api/recommendations/v2/recommendations (read again (1. Agotado))

`X-Correlation-ID: f24-08-1e15aa2d933a4f4a`, at 2026-11-24T03:33:08Z

```http
GET https://127.0.0.1/api/recommendations/v2/recommendations?customer_id=00000000-0000-0000-0000-000000000003&store_id=3
X-Correlation-ID: f24-08-1e15aa2d933a4f4a
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
X-Correlation-ID: f24-08-1e15aa2d933a4f4a

{
  "decision_id": "967eb947-3c6c-43aa-9204-edd2545b84bb",
  "customer_id": "00000000-0000-0000-0000-000000000003",
  "status": "recommended",
  "computed_at": "2026-11-24T03:33:03.226584Z",
  "window": {
    "days": 180,
    "start": "2026-05-28T03:33:03.226584Z",
    "end": "2026-11-24T03:33:03.226584Z"
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
      "count": 22
    },
    {
      "reason": "PERSONALIZATION_WITHDRAWN",
      "count": 1
    }
  ],
  "items": [
    {
      "product_id": 1,
      "category_id": 1,
      "kind": "primary",
      "position": 1,
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
      "position": 2,
      "factors": {
        "signals_matched": 1,
        "segment_buyers": 1,
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
          "segment_buyers": 1,
          "category_id": null,
          "share": null
        }
      ]
    },
    {
      "product_id": 5,
      "category_id": 5,
      "kind": "primary",
      "position": 3,
      "factors": {
        "signals_matched": 1,
        "segment_buyers": 1,
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
          "segment_buyers": 1,
          "category_id": null,
          "share": null
        }
      ]
    },
    {
      "product_id": 2,
      "category_id": 2,
      "kind": "elsewhere",
      "position": 4,
      "factors": {
        "signals_matched": 1,
        "segment_buyers": 2,
        "category_share": null
      },
      "availability": {
        "store_id": 13,
        "channel_id": null,
        "quantity_on_hand": 25
      },
      "alternative_for": null,
      "unavailable_reason": "out_of_stock",
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

<a id="exchange-14"></a>
## 14. GET /api/stock/v1/stock-levels/3/2 (stock level (1. Agotado))

`X-Correlation-ID: f24-08-dee42d4eb8944c0f`, at 2026-11-24T03:33:08Z

```http
GET https://127.0.0.1/api/stock/v1/stock-levels/3/2
X-Correlation-ID: f24-08-dee42d4eb8944c0f
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
x-correlation-id: f24-08-dee42d4eb8944c0f

{
  "store_id": 3,
  "product_id": 2,
  "quantity_on_hand": 0,
  "updated_at": "2026-11-24T03:33:03.157985Z"
}
```

<a id="exchange-15"></a>
## 15. POST /api/stock/v1/stock-movements (RECEIPT (restock))

`X-Correlation-ID: f24-08-04fb182f867c49f3`, at 2026-11-24T03:33:08Z

```http
POST https://127.0.0.1/api/stock/v1/stock-movements
X-Correlation-ID: f24-08-04fb182f867c49f3
Idempotency-Key: f3205c35-5555-42cd-9912-0bfd054cbbed
Authorization: Bearer <masked #4>
Accept: application/json
Content-Type: application/json

{
  "movement_type": "RECEIPT",
  "store_id": 3,
  "product_id": 2,
  "quantity": 10,
  "reference": "F24-08 (#831) restock",
  "note": "F24-08 (#831): restores the counted quantity"
}
```

```http
HTTP 201
Content-Type: application/json
x-correlation-id: f24-08-04fb182f867c49f3

{
  "movements": [
    {
      "movement_id": 37,
      "movement_type": "RECEIPT",
      "store_id": 3,
      "product_id": 2,
      "quantity_delta": 10,
      "quantity_before": 0,
      "quantity_after": 10,
      "transfer_id": null,
      "reference": "F24-08 (#831) restock",
      "reason": null,
      "counted_by": null,
      "note": "F24-08 (#831): restores the counted quantity",
      "reversal_id": null,
      "recorded_by": "b914c065-b19e-4372-a920-9888f8e6963e",
      "recorded_at": "2026-11-24T03:33:08.499553Z"
    }
  ]
}
```

<a id="exchange-16"></a>
## 16. GET /api/recommendations/v2/recommendations (poll (2. Reabastecido))

`X-Correlation-ID: f24-08-2ccf1bbef4d24d15`, at 2026-11-24T03:33:08Z

```http
GET https://127.0.0.1/api/recommendations/v2/recommendations?customer_id=00000000-0000-0000-0000-000000000003&store_id=3
X-Correlation-ID: f24-08-2ccf1bbef4d24d15
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
X-Correlation-ID: f24-08-2ccf1bbef4d24d15

{
  "decision_id": "0fd3af51-15c3-463d-b7af-b057ca631c26",
  "customer_id": "00000000-0000-0000-0000-000000000003",
  "status": "recommended",
  "computed_at": "2026-11-24T03:33:08.514964Z",
  "window": {
    "days": 180,
    "start": "2026-05-28T03:33:08.514964Z",
    "end": "2026-11-24T03:33:08.514964Z"
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
      "product_id": 1,
      "category_id": 1,
      "kind": "primary",
      "position": 1,
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
      "product_id": 2,
      "category_id": 2,
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
        "quantity_on_hand": 10
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
        "segment_buyers": 1,
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
          "segment_buyers": 1,
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
        "segment_buyers": 1,
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
          "segment_buyers": 1,
          "category_id": null,
          "share": null
        }
      ]
    }
  ]
}
```

<a id="exchange-17"></a>
## 17. GET /api/recommendations/v2/recommendations (read after the bound (2. Reabastecido))

`X-Correlation-ID: f24-08-2a738742749f46ee`, at 2026-11-24T03:33:13Z

```http
GET https://127.0.0.1/api/recommendations/v2/recommendations?customer_id=00000000-0000-0000-0000-000000000003&store_id=3
X-Correlation-ID: f24-08-2a738742749f46ee
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
X-Correlation-ID: f24-08-2a738742749f46ee

{
  "decision_id": "0fd3af51-15c3-463d-b7af-b057ca631c26",
  "customer_id": "00000000-0000-0000-0000-000000000003",
  "status": "recommended",
  "computed_at": "2026-11-24T03:33:08.514964Z",
  "window": {
    "days": 180,
    "start": "2026-05-28T03:33:08.514964Z",
    "end": "2026-11-24T03:33:08.514964Z"
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
      "product_id": 1,
      "category_id": 1,
      "kind": "primary",
      "position": 1,
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
      "product_id": 2,
      "category_id": 2,
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
        "quantity_on_hand": 10
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
        "segment_buyers": 1,
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
          "segment_buyers": 1,
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
        "segment_buyers": 1,
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
          "segment_buyers": 1,
          "category_id": null,
          "share": null
        }
      ]
    }
  ]
}
```

<a id="exchange-18"></a>
## 18. GET /api/recommendations/v2/recommendations (read again (2. Reabastecido))

`X-Correlation-ID: f24-08-a6735d1934c34049`, at 2026-11-24T03:33:13Z

```http
GET https://127.0.0.1/api/recommendations/v2/recommendations?customer_id=00000000-0000-0000-0000-000000000003&store_id=3
X-Correlation-ID: f24-08-a6735d1934c34049
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
X-Correlation-ID: f24-08-a6735d1934c34049

{
  "decision_id": "0fd3af51-15c3-463d-b7af-b057ca631c26",
  "customer_id": "00000000-0000-0000-0000-000000000003",
  "status": "recommended",
  "computed_at": "2026-11-24T03:33:08.514964Z",
  "window": {
    "days": 180,
    "start": "2026-05-28T03:33:08.514964Z",
    "end": "2026-11-24T03:33:08.514964Z"
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
      "product_id": 1,
      "category_id": 1,
      "kind": "primary",
      "position": 1,
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
      "product_id": 2,
      "category_id": 2,
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
        "quantity_on_hand": 10
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
        "segment_buyers": 1,
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
          "segment_buyers": 1,
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
        "segment_buyers": 1,
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
          "segment_buyers": 1,
          "category_id": null,
          "share": null
        }
      ]
    }
  ]
}
```

<a id="exchange-19"></a>
## 19. GET /api/stock/v1/stock-levels/3/2 (stock level (2. Reabastecido))

`X-Correlation-ID: f24-08-c24874e6e295491d`, at 2026-11-24T03:33:13Z

```http
GET https://127.0.0.1/api/stock/v1/stock-levels/3/2
X-Correlation-ID: f24-08-c24874e6e295491d
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
x-correlation-id: f24-08-c24874e6e295491d

{
  "store_id": 3,
  "product_id": 2,
  "quantity_on_hand": 10,
  "updated_at": "2026-11-24T03:33:08.499553Z"
}
```

<a id="exchange-20"></a>
## 20. DELETE /api/auth/v1/sessions/current (sign out, reader)

`X-Correlation-ID: f24-08-3b821ce53ae64b39`, at 2026-11-24T03:33:14Z

```http
DELETE https://127.0.0.1/api/auth/v1/sessions/current
X-Correlation-ID: f24-08-3b821ce53ae64b39
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 204
X-Correlation-ID: f24-08-3b821ce53ae64b39


```

<a id="exchange-21"></a>
## 21. DELETE /api/auth/v1/sessions/current (sign out, planner)

`X-Correlation-ID: f24-08-7df18a10339d4ae8`, at 2026-11-24T03:33:15Z

```http
DELETE https://127.0.0.1/api/auth/v1/sessions/current
X-Correlation-ID: f24-08-7df18a10339d4ae8
Authorization: Bearer <masked #4>
Accept: application/json


```

```http
HTTP 204
X-Correlation-ID: f24-08-7df18a10339d4ae8


```
