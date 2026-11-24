# F29-03 snapshot before the sale: requests and responses

<a id="exchange-1"></a>
## 1. POST /api/auth/v1/sessions (sign in, service reader)

`X-Correlation-ID: f29-03-c48b3b14ba4443f5`, at 2026-11-24T04:52:36Z

```http
POST https://mosaiq.maxthecoder.online/api/auth/v1/sessions
X-Correlation-ID: f29-03-c48b3b14ba4443f5
Accept: application/json
Content-Type: application/json

{
  "email": "f29-03-store-manager@example.test",
  "password": "<masked #3>",
  "device_label": "F29-03 snapshot before the sale"
}
```

```http
HTTP 201
Content-Type: application/json
x-correlation-id: f29-03-c48b3b14ba4443f5

{
  "access_token": "<masked #1>",
  "refresh_token": "<masked #2>",
  "token_type": "Bearer",
  "expires_in": 600,
  "expires_at": "2026-11-24T05:02:34Z"
}
```

<a id="exchange-2"></a>
## 2. GET /login (the sign-in form)

`X-Correlation-ID: f29-03-ae9f346421ad4348`, at 2026-11-24T04:52:36Z

```http
GET https://mosaiq.maxthecoder.online/login
X-Correlation-ID: f29-03-ae9f346421ad4348
Accept: text/html
User-Agent: mosaiq-acceptance/1


```

```http
HTTP 200
Content-Type: text/html; charset=utf-8

(HTML page, 2734 bytes: pages/002.html)
```

<a id="exchange-3"></a>
## 3. POST /login (portal sign-in)

`X-Correlation-ID: f29-03-5ce0ccf77cc848dd`, at 2026-11-24T04:52:37Z

```http
POST https://mosaiq.maxthecoder.online/login
X-Correlation-ID: f29-03-5ce0ccf77cc848dd
Accept: text/html
User-Agent: mosaiq-acceptance/1
Content-Type: application/x-www-form-urlencoded
Origin: https://mosaiq.maxthecoder.online

email=f29-03-store-manager@example.test&password=<masked #3>&csrf_token=<masked #4>
```

```http
HTTP 200
Content-Type: text/html; charset=utf-8

(HTML page, 8375 bytes: pages/003.html)
```

<a id="exchange-4"></a>
## 4. GET /api/auth/openapi.yaml (version)

`X-Correlation-ID: f29-03-ff174f8b221d4c6b`, at 2026-11-24T04:52:37Z

```http
GET https://mosaiq.maxthecoder.online/api/auth/openapi.yaml
X-Correlation-ID: f29-03-ff174f8b221d4c6b


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f29-03-ff174f8b221d4c6b

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

<a id="exchange-5"></a>
## 5. GET /api/users/openapi.yaml (version)

`X-Correlation-ID: f29-03-2aa3772350324cf0`, at 2026-11-24T04:52:38Z

```http
GET https://mosaiq.maxthecoder.online/api/users/openapi.yaml
X-Correlation-ID: f29-03-2aa3772350324cf0


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f29-03-2aa3772350324cf0

openapi: 3.1.0
info:
  title: users-service
  version: 1.4.0
  description: >-
    Owns user accounts, password changes, deactivation, the read-only role
    vocabulary, the atomic administrator transfer, and the stores each floor
    user is assigned to. Responses never expose credentials.


    Every permission change records a `PERMISSION_CHANGED` domain event in the
    transaction that makes it (ADR-0072): a role assigned through
    `PATCH /v1/users/{user_id}`, an administrator transfer (one event for each
    user whose role moved), a deactivation and a reactivation, and a
    store-assignment replacement (one event for the stores added and one for
    the stores removed). The event names the acting administrator, the
    subject user and the role codes or store ids, never a password or a
    token. When it cannot be recorded the change is rolled back and the call
    is 503 `audit_unavailable`.
security:
  - bearer: []
tags:
  - name: Users
  - name: Roles
  - name: Administrator transfers
  - name: Store assignments
  - name: Meta
paths:
  /v1/users:
    get:
      tags: [Users]
      operationId: listUsers
      summary: List users
      x-mosaiq-roles: [ADMIN, AUDITOR]
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
        - name: role
          in: query
          description: Return only users with this role code.
          schema:
            $ref: '#/components/schemas/RoleCode'
        - name: active
          in: query
          description: Return only active or inactive users.
          schema:
            type: boolean
        - name: q
          in: query
          description: Case-insensitive search over name and email.
          schema:
            type: string
            minLength: 1
            maxLength: 160
        - $ref: '#/components/parameters/Page'
        - $ref: '#/components/parameters/Size'
      responses:
        '200':
          description: A page of users.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/UserPage'
              examples:
                users:
                  $ref: '#/components/examples/UsersJson'
            application/xml:
              schema:
                $ref: '#/components/schemas/UserPage'
              examples:
                users:
                  $ref: '#/components/examples/UsersXml'
        '401':
          $ref: '#/components/responses/AuthenticationErrors'
        '403':
          $ref: '#/components/responses/ForbiddenRole'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '422':
          $ref: '#/components/responses/InvalidPagination'
        '503':
          $ref: '#/components/responses/DependencyUnavailable'
    post:
      tags: [Users]
      operationId: createUser
      summary: Create a user
      description: >-
        Creates an active user. ADMIN is syntactically accepted so the
        single-administrator rule can return single_administrator rather than
        disguising a domain conflict as malformed input.
      x-mosaiq-roles: [ADMIN]
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/CreateUserRequest'
            examples:
              create_user:
                $ref: '#/components/examples/CreateUserRequestJson'
          application/xml:
            schema:
              $ref: '#/components/schemas/CreateUserRequest'
            examples:
              create_user:
                $ref: '#/components/examples/CreateUserRequestXml'
      responses:
        '201':
          description: The created user, without the password.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/User'
              examples:
                created_user:
                  $ref: '#/components/examples/CreatedUserJson'
            application/xml:
              schema:
                $ref: '#/components/schemas/User'
              examples:
                created_user:
                  $ref: '#/components/examples/CreatedUserXml'
        '400':
          $ref: '#/components/responses/MalformedBody'
        '401':
          $ref: '#/components/responses/AuthenticationErrors'
        '403':
          $ref: '#/components/responses/ForbiddenRole'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '409':
          $ref: '#/components/responses/CreateUserConflicts'
        '413':
          $ref: '#/components/responses/PayloadTooLarge'
        '415':
          $ref: '#/components/responses/UnsupportedMediaType'
        '422':
          $ref: '#/components/responses/CreateUserValidationErrors'
        '503':
          $ref: '#/components/responses/DependencyUnavailable'
  /v1/users/me:
    get:
      tags: [Users]
      operationId: getCurrentUser
      summary: Get the caller's profile
      x-mosaiq-roles:
        - ADMIN
        - ANALYST
        - STORE_MANAGER
        - MARKETING
        - INVENTORY_PLANNER
        - AUDITOR
        - CUSTOMER
        - SALES_ASSOCIATE
        - LOYALTY_OFFICER
        - COMMERCIAL_SUPERVISOR
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
      responses:
        '200':
          description: The caller's current user record.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/User'
              examples:
                current_user:
                  $ref: '#/components/examples/Current
… (69821 more characters)
```

<a id="exchange-6"></a>
## 6. GET /api/stores/openapi.yaml (version)

`X-Correlation-ID: f29-03-89e39f073bb443e2`, at 2026-11-24T04:52:38Z

```http
GET https://mosaiq.maxthecoder.online/api/stores/openapi.yaml
X-Correlation-ID: f29-03-89e39f073bb443e2


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f29-03-89e39f073bb443e2

openapi: 3.1.0
info:
  title: stores-service
  version: 1.4.0
  description: >-
    Publishes the store directory and lets an administrator set or clear the
    one geofenced location that belongs to a store (RN-53). Also publishes, and
    lets an administrator replace, a store's operating status, weekly opening
    hours, enabled channel ids and commercial rules, each as its own
    sub-resource of `/v1/stores/{store_id}`.
security:
  - bearer: []
tags:
  - name: Stores
  - name: Meta
paths:
  /v1/stores:
    get:
      tags: [Stores]
      summary: List the store directory
      description: >-
        Returns a page of stores. Cache misses read PostgreSQL, so a database
        failure remains visible even though successful reads may be cached. A
        filter value outside its schema returns 422 `invalid_query`, naming the
        parameter in `details`.
      operationId: listStores
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
        - $ref: '#/components/parameters/Active'
        - $ref: '#/components/parameters/City'
        - $ref: '#/components/parameters/Page'
        - $ref: '#/components/parameters/Size'
      responses:
        '200':
          description: A page of stores.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/StorePage'
              examples:
                directory_page:
                  externalValue: ./examples/directory-page.json
            application/xml:
              schema:
                $ref: '#/components/schemas/StorePage'
              examples:
                directory_page:
                  externalValue: ./examples/directory-page.xml
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '422':
          $ref: '#/components/responses/StoreListValidation'
        '429':
          $ref: '#/components/responses/RateLimited'
        '503':
          $ref: '#/components/responses/OperationUnavailable'
  /v1/stores/{store_id}:
    get:
      tags: [Stores]
      summary: Get one store
      description: >-
        Returns the store and its location when one is set. An unset location
        is null in JSON and omitted in XML.
      operationId: getStore
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
        - $ref: '#/components/parameters/StoreId'
      responses:
        '200':
          description: The requested store.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Store'
              examples:
                store_with_location:
                  externalValue: ./examples/store-with-location.json
                store_without_location:
                  externalValue: ./examples/store-without-location.json
            application/xml:
              schema:
                $ref: '#/components/schemas/Store'
              examples:
                store_with_location:
                  externalValue: ./examples/store-with-location.xml
                store_without_location:
                  externalValue: ./examples/store-without-location.xml
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '404':
          $ref: '#/components/responses/StoreNotFound'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '429':
          $ref: '#/components/responses/RateLimited'
        '503':
          $ref: '#/components/responses/OperationUnavailable'
  /v1/stores/{store_id}/location:
    put:
      tags: [Stores]
      summary: Set a store location
      description: >-
        Sets or replaces the store's single location. Only ADMIN may write
        this fact. All three location fields are replaced together.
      operationId: putStoreLocation
      x-mosaiq-roles: [ADMIN]
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
        - $ref: '#/components/parameters/StoreId'
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/LocationUpdate'
            examples:
              set_location:
                externalValue: ./examples/set-location.request.json
          application/xml:
            schema:
              $ref: '#/components/schemas/LocationUpdate'
            examples:
              set_location:
                externalValue: ./examples/set-location.request.xml
      responses:
        '200':
          description: The store with its newly set location.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Store'
              examples:
                set_location:
                  externalValue: ./examples/set-location.json
            application/xml:
              schema:
                $ref: '#/components/schemas/Store'
              examples:
                set_location:
                  externalValue: ./examples/set-locatio
… (40593 more characters)
```

<a id="exchange-7"></a>
## 7. GET /api/products/openapi.yaml (version)

`X-Correlation-ID: f29-03-0964b4482c44488e`, at 2026-11-24T04:52:38Z

```http
GET https://mosaiq.maxthecoder.online/api/products/openapi.yaml
X-Correlation-ID: f29-03-0964b4482c44488e


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f29-03-0964b4482c44488e

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

<a id="exchange-8"></a>
## 8. GET /api/stock/openapi.yaml (version)

`X-Correlation-ID: f29-03-5c54f32e30be427e`, at 2026-11-24T04:52:38Z

```http
GET https://mosaiq.maxthecoder.online/api/stock/openapi.yaml
X-Correlation-ID: f29-03-5c54f32e30be427e


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f29-03-5c54f32e30be427e

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

<a id="exchange-9"></a>
## 9. GET /api/customers/openapi.yaml (version)

`X-Correlation-ID: f29-03-9c3ebf5eca754c2f`, at 2026-11-24T04:52:38Z

```http
GET https://mosaiq.maxthecoder.online/api/customers/openapi.yaml
X-Correlation-ID: f29-03-9c3ebf5eca754c2f


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f29-03-9c3ebf5eca754c2f

openapi: 3.1.0
info:
  title: customers-service
  version: 1.8.1
  description: >-
    Registers, searches, reads and corrects customers under RN-08. Customers
    are never deleted through services; a mistaken registration is corrected
    with PATCH. Also issues and revokes loyalty cards, whose QR code is an
    opaque identifier. Floor roles read customers through the floor view, which
    carries only the customer's own fields; segment, RFM, interests,
    restrictions and consent are read from the services that own them.


    **Anonymization (ADR-0066 section 3).** A customer is never deleted. After a
    request, or after 36 months with no sale and no check-in, an administrator
    anonymizes the customer in place with `PUT /v1/customers/{customer_id}/anonymization`:
    the name becomes the fixed placeholder `Anonymized customer`, the email and
    phone are removed, the account link is cleared, and every active loyalty card
    is revoked. The key, `registered_on`, the registration channel, the sales and
    the segment history stay, so every figure built on them still reconciles.
    The operation is irreversible and idempotent. From then on every read returns
    the placeholder name and no email and no phone; a correction
    or a new loyalty card is refused with 409 `customer_anonymized`; the customer
    is flagged `anonymized` in `api_customers.customer_ref_v1`, and the owner
    announces it on `stream:customers.v1.anonymized`
    (`contracts/streams/customers.anonymized.v1.md`).
servers:
  - url: /api/customers
security:
  - bearer: []
tags:
  - name: Customers
  - name: Loyalty cards
  - name: Meta
paths:
  /v1/customers:
    get:
      tags: [Customers]
      operationId: searchCustomers
      summary: Search customers
      description: >-
        Searches the customer directory. q is matched case-insensitively as a
        substring of name, email or phone; percent (%) and underscore (_) in q
        match themselves literally and are not SQL wildcards. email is an
        exact match that ignores letter case. When both filters are present,
        both must match. A filter value outside its schema, or an empty email,
        returns 422 `invalid_query`, naming the parameter in `details`.
      x-mosaiq-roles: [ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR, LOYALTY_OFFICER]
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
        - name: q
          in: query
          description: >-
            Case-insensitive literal substring matched over name, email or
            phone. Percent and underscore are literal characters.
          schema:
            type: string
            minLength: 1
            maxLength: 160
          example: '100% Club_'
        - name: email
          in: query
          description: Exact customer email match, ignoring letter case.
          schema:
            type: string
            maxLength: 160
          example: ana@example.com
        - $ref: '#/components/parameters/Page'
        - $ref: '#/components/parameters/Size'
      responses:
        '200':
          description: A page of matching customers.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Customers'
              examples:
                literal_percent_and_underscore:
                  externalValue: ./examples/search-literal-percent.json
            application/xml:
              schema:
                $ref: '#/components/schemas/Customers'
              examples:
                literal_percent_and_underscore:
                  externalValue: ./examples/search-literal-percent.xml
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '422':
          $ref: '#/components/responses/CustomerListValidation'
        '429':
          $ref: '#/components/responses/RateLimited'
        '503':
          $ref: '#/components/responses/ProtectedDatabaseUnavailable'
    post:
      tags: [Customers]
      operationId: registerCustomer
      summary: Register a customer
      description: >-
        Registers one customer. registered_on is optional and defaults to the
        server's current date. A date after the server's current date is
        refused.
      x-mosaiq-roles: [ADMIN, STORE_MANAGER, LOYALTY_OFFICER]
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/RegisterCustomerRequest'
            examples:
              registration:
                externalValue: ./examples/register-customer.request.json
          application/xml:
            schema:
              $ref: '#/components/schemas/RegisterCustomerRequest'
            examples:
              registration:
                externalValue: ./examples/register-customer.request.xml
      responses:
        '201':
          description: Customer registered.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
            Location:
              description: URI of the registered customer.
              required: true
              schema:
                type: string
                format: uri-reference
              example: /v1/customers/6b8c1d78-38d1-45ba-8805-ddaf3a6c62b4
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Customer'
              examples:
                registered_customer:
                  externalValue: ./examples/register-customer.json
            application/xml:
              schema:
                $ref: '#/components/schemas/Customer'
              ex
… (62260 more characters)
```

<a id="exchange-10"></a>
## 10. GET /api/sales/openapi.yaml (version)

`X-Correlation-ID: f29-03-851c8d744ca94eab`, at 2026-11-24T04:52:39Z

```http
GET https://mosaiq.maxthecoder.online/api/sales/openapi.yaml
X-Correlation-ID: f29-03-851c8d744ca94eab


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f29-03-851c8d744ca94eab

openapi: 3.1.0
info:
  title: sales-service
  version: 1.10.1
  description: >-
    Records a sale exactly once, all or nothing, and reads sales and the channel
    vocabulary back (RN-11 to RN-13, RN-37). A sale is never edited or deleted:
    a cancellation or a return is recorded beside it as a reversal, an
    append-only fact, and every reader counts net units (ADR-0062). A sales file
    reaches the service as a sale import: its rows travel as numbered chunks under
    the 64 KiB body cap and a worker applies them one row at a time through the
    same rule, each row accepted or refused on its own and every refusal reported
    by line, field and reason (ADR-0070). A finished import can then be reconciled:
    a sale it made that looks like a sale recorded earlier under another source id is
    listed as a case for a person to settle, never merged or removed silently, and the
    import's quality report counts its failures by code (F26-09). The
    promotions applied in sales are totalled per store and promotion, in
    aggregate and net of reversals, with no customer named (F27-16).
security:
  - bearer: []
tags:
  - name: Sales
  - name: Sale reversals
  - name: Sale imports
  - name: Sale reconciliation
  - name: Sale promotion totals
  - name: Channels
  - name: Meta
paths:
  /v1/sales:
    post:
      tags: [Sales]
      summary: Record a sale
      description: >-
        Records the whole sale in one transaction, or nothing: the header, every
        line and the stock consumed by each line commit together. `occurred_at` must
        carry an offset, must not be in the future and must not precede the
        customer's registration. Every product must be active, every quantity
        positive and every `unit_price` the price actually charged (RN-13), positive
        with two decimals. Lines refuse an empty list (`no_lines`) and a product
        twice (`duplicate_product_line`); a line-level refusal names the line in
        `details`, with a zero-based index as in `lines[1].quantity`. The schema
        does not cap or constrain these values, so that the service answers with its
        own code.

        An anonymized customer. A customer that customers-service has anonymized
        (`anonymized` in `api_customers.customer_ref_v1`, ADR-0066 section 3) is
        refused with 422 `anonymized_customer` (detail `customer_id`,
        `anonymized`), and nothing is stored. Its past sales stay as they are,
        and their reversals are still recorded.

        Promotions and discounts (F25-05, RN-13). A line's `unit_price` stays the
        price actually charged; the list price it is compared with is the product's
        `list_price` read in the sale's transaction, and the service keeps it with
        the line, so a discount can be reported later. A line charged below that
        list price must name the promotion it relies on in `promotion_id`; one that
        names none is 422 `discount_without_promotion`. A named promotion is always
        validated, whatever the line's price: it must be a promotion of an
        `ACTIVE` or `FINISHED` campaign for the sale's store and channel
        (`api_campaigns.promotion_window_v1`), and the sale's own day,
        `occurred_at` in the service's business time zone, must lie within its
        `starts_on` and `ends_on`, both inclusive; otherwise 422
        `promotion_not_applicable`. The day that counts is the sale's, not the day
        the request arrives, so a sale queued offline on a promotion's last day
        is still accepted when it syncs later. A line names at most one promotion, and a
        promotion does not combine with any other discount. The discount allowed is
        the smaller of the promotion's `discount_percent` and the store's
        `max_discount_percent` (no cap of the store's own when it has none): the
        lowest price accepted is the list price times (1 - percent / 100), rounded
        half up to the cent. A line charged below it is 422
        `discount_above_promotion` when the promotion is the tighter limit, and
        `discount_above_store_cap` when the store's rule is. Each of these names
        the line in `details` (`lines[1].promotion_id` or `lines[1].unit_price`),
        and nothing is recorded. A line charged at or above its list price
        needs no promotion and is accepted as before. Only this operation
        applies the rule: a sale import keeps the rules it states.

        Who may record where. `STORE_MANAGER` and `SALES_ASSOCIATE` work only in
        their assigned stores: a sale for another store is 403 `forbidden_store`
        (detail `store_id`, `other_store`), decided before the store is looked up,
        so a caller learns nothing about stores it is not assigned to. `ADMIN` is
        not confined (ADR-0060).

        Idempotency. The `Idempotency-Key` becomes `source_transaction_id =
        'svc-sales:' || lowercase(key)`. The service hashes the caller's `sub`, the
        ids, `occurred_at` in UTC and the lines sorted by `product_id` with
        two-decimal prices and each line's `promotion_id`, so a JSON retry and an XML retry hash the same. The same
        key with the same hash is a replay: 200 with the original sale and
        `Idempotent-Replayed: true`. The same key with a different hash is 409
        `idempotency_key_reused`. Only accepted sales are remembered, so a refused
        request may be retried with the same key.

        A client retries only on a network error, a timeout or a 503, always with
        the same key, and treats 409 and 422 as final. A 503 `database_unavailable`
        is retried like any other 503.
      operationId: recordSale
      x-mosaiq-roles:
        - ADMIN
        - STORE_MANAGER
        - SALES_ASSOCIATE
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
        - $ref: '#/components/parameters/IdempotencyKey'
      requestBody:
        required: true
        content:
          applicatio
… (122151 more characters)
```

<a id="exchange-11"></a>
## 11. GET /api/stock/v1/stock-levels/30/1 (stock level)

`X-Correlation-ID: f29-03-05910a67c2784164`, at 2026-11-24T04:52:54Z

```http
GET https://mosaiq.maxthecoder.online/api/stock/v1/stock-levels/30/1
X-Correlation-ID: f29-03-05910a67c2784164
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
x-correlation-id: f29-03-05910a67c2784164

{
  "store_id": 30,
  "product_id": 1,
  "quantity_on_hand": 40,
  "updated_at": "2026-11-24T03:51:17.700846Z"
}
```

<a id="exchange-12"></a>
## 12. GET /catalog/stock (portal stock, SKU-00001)

`X-Correlation-ID: f29-03-443a2dc710f64602`, at 2026-11-24T04:52:54Z

```http
GET https://mosaiq.maxthecoder.online/catalog/stock?store=30&q=SKU-00001
X-Correlation-ID: f29-03-443a2dc710f64602
Accept: text/html
User-Agent: mosaiq-acceptance/1


```

```http
HTTP 200
Content-Type: text/html; charset=utf-8

(HTML page, 11081 bytes: pages/012.html)
```

<a id="exchange-13"></a>
## 13. GET /api/stock/v1/stock-levels/30/2 (stock level)

`X-Correlation-ID: f29-03-15c5288c11884719`, at 2026-11-24T04:53:17Z

```http
GET https://mosaiq.maxthecoder.online/api/stock/v1/stock-levels/30/2
X-Correlation-ID: f29-03-15c5288c11884719
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 200
Content-Type: application/json
x-correlation-id: f29-03-15c5288c11884719

{
  "store_id": 30,
  "product_id": 2,
  "quantity_on_hand": 41,
  "updated_at": "2026-11-24T03:51:17.700846Z"
}
```

<a id="exchange-14"></a>
## 14. GET /catalog/stock (portal stock, SKU-00002)

`X-Correlation-ID: f29-03-b84f317e0fa846b3`, at 2026-11-24T04:53:18Z

```http
GET https://mosaiq.maxthecoder.online/catalog/stock?store=30&q=SKU-00002
X-Correlation-ID: f29-03-b84f317e0fa846b3
Accept: text/html
User-Agent: mosaiq-acceptance/1


```

```http
HTTP 200
Content-Type: text/html; charset=utf-8

(HTML page, 11081 bytes: pages/014.html)
```

<a id="exchange-15"></a>
## 15. GET /catalog/customers/00000000-0000-0000-0000-000000000028/profile (customer profile)

`X-Correlation-ID: f29-03-834126e21a8d46da`, at 2026-11-24T04:53:33Z

```http
GET https://mosaiq.maxthecoder.online/catalog/customers/00000000-0000-0000-0000-000000000028/profile?window=30
X-Correlation-ID: f29-03-834126e21a8d46da
Accept: text/html
User-Agent: mosaiq-acceptance/1


```

```http
HTTP 200
Content-Type: text/html; charset=utf-8

(HTML page, 19802 bytes: pages/015.html)
```

<a id="exchange-16"></a>
## 16. DELETE /api/auth/v1/sessions/current (sign out, service reader)

`X-Correlation-ID: f29-03-f37f9b8e30b84d72`, at 2026-11-24T04:53:48Z

```http
DELETE https://mosaiq.maxthecoder.online/api/auth/v1/sessions/current
X-Correlation-ID: f29-03-f37f9b8e30b84d72
Authorization: Bearer <masked #1>
Accept: application/json


```

```http
HTTP 204
x-correlation-id: f29-03-f37f9b8e30b84d72


```
