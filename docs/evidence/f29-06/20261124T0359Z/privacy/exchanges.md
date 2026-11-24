# F29-06 Privacidad: requests and responses

<a id="exchange-1"></a>
## 1. GET /api/auth/openapi.yaml (version)

`X-Correlation-ID: f29-06-75d72e8ce8164a1d`, at 2026-11-24T03:59:30Z

```http
GET https://127.0.0.1/api/auth/openapi.yaml
X-Correlation-ID: f29-06-75d72e8ce8164a1d


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f29-06-75d72e8ce8164a1d

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

<a id="exchange-2"></a>
## 2. GET /api/users/openapi.yaml (version)

`X-Correlation-ID: f29-06-a87ba6367f7444a7`, at 2026-11-24T03:59:30Z

```http
GET https://127.0.0.1/api/users/openapi.yaml
X-Correlation-ID: f29-06-a87ba6367f7444a7


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f29-06-a87ba6367f7444a7

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

<a id="exchange-3"></a>
## 3. GET /api/products/openapi.yaml (version)

`X-Correlation-ID: f29-06-1d73dba5aaa04929`, at 2026-11-24T03:59:30Z

```http
GET https://127.0.0.1/api/products/openapi.yaml
X-Correlation-ID: f29-06-1d73dba5aaa04929


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f29-06-1d73dba5aaa04929

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

<a id="exchange-4"></a>
## 4. GET /api/categories/openapi.yaml (version)

`X-Correlation-ID: f29-06-c0402be578bc41b5`, at 2026-11-24T03:59:30Z

```http
GET https://127.0.0.1/api/categories/openapi.yaml
X-Correlation-ID: f29-06-c0402be578bc41b5


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f29-06-c0402be578bc41b5

openapi: 3.1.0
info:
  title: categories-service
  version: 1.2.0
  description: >-
    Reads the category tree and lets an administrator create, move, rename and
    delete categories without a cycle and without orphaning products, children
    or customer interests (RN-33, RN-06, RN-07).
security:
  - bearer: []
tags:
  - name: Categories
  - name: Meta
paths:
  /v1/categories:
    get:
      tags: [Categories]
      summary: List categories
      description: >-
        Returns a page of categories ordered by `category_id`. Without `parent_id`
        it lists every category; with it, only the direct children of that category.
        A `parent_id` that matches no category gives an empty page. Cache misses
        read PostgreSQL, so a database failure stays visible even though successful
        reads may be cached. A filter value outside its schema returns 422
        `invalid_query`, naming the parameter in `details`.
      operationId: listCategories
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
        - $ref: '#/components/parameters/ParentId'
        - $ref: '#/components/parameters/Page'
        - $ref: '#/components/parameters/Size'
      responses:
        '200':
          description: A page of categories.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/CategoryPage'
              examples:
                children_of_dairy:
                  externalValue: ./examples/list-categories.json
            application/xml:
              schema:
                $ref: '#/components/schemas/CategoryPage'
              examples:
                children_of_dairy:
                  externalValue: ./examples/list-categories.xml
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '422':
          $ref: '#/components/responses/CategoryListValidation'
        '429':
          $ref: '#/components/responses/RateLimited'
        '503':
          $ref: '#/components/responses/OperationUnavailable'
    post:
      tags: [Categories]
      summary: Create a category
      description: >-
        Creates a category with the caller-supplied `category_id`, as the web's
        catalog form does. `parent_category_id` is null or omitted for a root. The
        name is unique.
      operationId: createCategory
      x-mosaiq-roles:
        - ADMIN
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/CreateCategoryRequest'
            examples:
              category:
                externalValue: ./examples/create-category.request.json
          application/xml:
            schema:
              $ref: '#/components/schemas/CreateCategoryRequest'
            examples:
              category:
                externalValue: ./examples/create-category.request.xml
      responses:
        '201':
          description: The category was created.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
            Location:
              $ref: '#/components/headers/Location'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Category'
              examples:
                created_category:
                  externalValue: ./examples/create-category.json
            application/xml:
              schema:
                $ref: '#/components/schemas/Category'
              examples:
                created_category:
                  externalValue: ./examples/create-category.xml
        '400':
          $ref: '#/components/responses/MalformedBody'
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '409':
          $ref: '#/components/responses/CategoryCreateConflict'
        '413':
          $ref: '#/components/responses/PayloadTooLarge'
        '415':
          $ref: '#/components/responses/UnsupportedMediaType'
        '422':
          $ref: '#/components/responses/CategoryCreateRefused'
        '429':
          $ref: '#/components/responses/RateLimited'
        '503':
          $ref: '#/components/responses/OperationUnavailable'
  /v1/categories/{category_id}:
    get:
      tags: [Categories]
      summary: Get one category
      description: >-
        Returns the category. `parent_category_id` is null at a root and omitted in
        XML.
      operationId: getCategory
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
        - $ref: '#/components/parameters/CategoryId'
      responses:
        '200':
          description: The requested category.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Category'
              examples:
                category:
                  externalValue: ./examples/get-category.json
                root_category:
                  exte
… (25209 more characters)
```

<a id="exchange-5"></a>
## 5. GET /api/stores/openapi.yaml (version)

`X-Correlation-ID: f29-06-003eb73b284d4dbe`, at 2026-11-24T03:59:30Z

```http
GET https://127.0.0.1/api/stores/openapi.yaml
X-Correlation-ID: f29-06-003eb73b284d4dbe


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f29-06-003eb73b284d4dbe

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

<a id="exchange-6"></a>
## 6. GET /api/stock/openapi.yaml (version)

`X-Correlation-ID: f29-06-60b2ea1674ce46fd`, at 2026-11-24T03:59:30Z

```http
GET https://127.0.0.1/api/stock/openapi.yaml
X-Correlation-ID: f29-06-60b2ea1674ce46fd


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f29-06-60b2ea1674ce46fd

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

<a id="exchange-7"></a>
## 7. GET /api/customers/openapi.yaml (version)

`X-Correlation-ID: f29-06-70cad6f1f35f43d0`, at 2026-11-24T03:59:30Z

```http
GET https://127.0.0.1/api/customers/openapi.yaml
X-Correlation-ID: f29-06-70cad6f1f35f43d0


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f29-06-70cad6f1f35f43d0

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

<a id="exchange-8"></a>
## 8. GET /api/customer-preferences/openapi.yaml (version)

`X-Correlation-ID: f29-06-35de80742ab34e8e`, at 2026-11-24T03:59:30Z

```http
GET https://127.0.0.1/api/customer-preferences/openapi.yaml
X-Correlation-ID: f29-06-35de80742ab34e8e


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f29-06-35de80742ab34e8e

openapi: 3.1.0
info:
  title: customer-preferences-service
  version: 1.1.2
  description: >-
    Reads a customer's preference profile and replaces each of its two sets,
    preferred channels and interest categories, only as a whole (RN-54). Also
    records a customer's consent by purpose and their restrictions as an
    append-only, attributed log whose current state is published to the other
    boundaries (RN-73, ADR-0066), reads the history of those records and of the
    two sets, and returns declared and inferred interests apart, so that an
    inferred interest never stands in for a declared one.
security:
  - bearer: []
tags:
  - name: Preferences
  - name: Meta
paths:
  /v1/preference-profiles/{customer_id}:
    get:
      tags: [Preferences]
      summary: Get a customer's preference profile
      description: >-
        Returns the customer's preferred channels and interest categories. A
        customer with no preferences gets a profile with two empty lists, not a 404;
        only a customer absent from `api_customers.customer_ref_v1` is 404
        `customer_not_found`.
      operationId: getPreferenceProfile
      x-mosaiq-roles:
        - ADMIN
        - ANALYST
        - STORE_MANAGER
        - MARKETING
        - AUDITOR
        - LOYALTY_OFFICER
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
        - $ref: '#/components/parameters/CustomerId'
      responses:
        '200':
          description: The preference profile.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/PreferenceProfile'
              examples:
                profile:
                  externalValue: ./examples/get-profile.json
                empty_profile:
                  externalValue: ./examples/get-empty-profile.json
            application/xml:
              schema:
                $ref: '#/components/schemas/PreferenceProfile'
              examples:
                profile:
                  externalValue: ./examples/get-profile.xml
                empty_profile:
                  externalValue: ./examples/get-empty-profile.xml
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '404':
          $ref: '#/components/responses/CustomerNotFound'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '429':
          $ref: '#/components/responses/RateLimited'
        '503':
          $ref: '#/components/responses/OperationUnavailable'
  /v1/preference-profiles/{customer_id}/preferred-channels:
    put:
      tags: [Preferences]
      summary: Replace the preferred channels
      description: >-
        A PUT replaces the whole set: ids not in the list are removed, ids in it are
        added, and an empty list clears the set (in XML, a request element with no
        ids). Ids are sorted in the answer. The delete and the insert run in one
        transaction under a per-customer advisory lock, so two concurrent replaces
        leave exactly one of the two submitted sets, never a mixture. A duplicate id
        in the list is 422 `invalid_body`, with `details` naming the field. The
        schema does not check that ids exist, so that the service answers
        ``unknown_channel`` with its own code. The other set is untouched. A replace
        that changes the set is also recorded, with its before and after, in the
        preference history (`listPreferenceChanges`, planned), in the same transaction;
        the whole-set rule is unchanged. Answers 200 with the whole profile after the
        replace.
      operationId: replacePreferredChannels
      x-mosaiq-roles:
        - ADMIN
        - STORE_MANAGER
        - LOYALTY_OFFICER
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
        - $ref: '#/components/parameters/CustomerId'
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ReplacePreferredChannelsRequest'
            examples:
              replace:
                externalValue: ./examples/replace-preferred-channels.request.json
              clear:
                externalValue: ./examples/clear-preferred-channels.request.json
          application/xml:
            schema:
              $ref: '#/components/schemas/ReplacePreferredChannelsRequest'
            examples:
              replace:
                externalValue: ./examples/replace-preferred-channels.request.xml
              clear:
                externalValue: ./examples/clear-preferred-channels.request.xml
      responses:
        '200':
          description: The profile after the replace.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/PreferenceProfile'
              examples:
                replaced:
                  externalValue: ./examples/replace-preferred-channels.json
                cleared:
                  externalValue: ./examples/clear-preferred-channels.json
            application/xml:
              schema:
                $ref: '#/components/schemas/PreferenceProfile'
              examples:
                replaced:
                  externalValue: ./examples/replace-preferred-channels.xml
                cleared:
                  externalValue: ./examples/clear-preferred-channels.xml
        '400':
          $ref: '#/components/responses/MalformedBody'
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '404':
          $ref: '#/components/responses/CustomerNotFound'
        '406':
          $ref: '#/compon
… (64395 more characters)
```

<a id="exchange-9"></a>
## 9. GET /api/sales/openapi.yaml (version)

`X-Correlation-ID: f29-06-bfbd0b8bb5c342df`, at 2026-11-24T03:59:30Z

```http
GET https://127.0.0.1/api/sales/openapi.yaml
X-Correlation-ID: f29-06-bfbd0b8bb5c342df


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f29-06-bfbd0b8bb5c342df

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

<a id="exchange-10"></a>
## 10. GET /api/segments/openapi.yaml (version)

`X-Correlation-ID: f29-06-dac65566aabe4193`, at 2026-11-24T03:59:30Z

```http
GET https://127.0.0.1/api/segments/openapi.yaml
X-Correlation-ID: f29-06-dac65566aabe4193


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f29-06-dac65566aabe4193

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

<a id="exchange-11"></a>
## 11. GET /api/recommendations/openapi.yaml (version)

`X-Correlation-ID: f29-06-026442ea96ec4cdb`, at 2026-11-24T03:59:30Z

```http
GET https://127.0.0.1/api/recommendations/openapi.yaml
X-Correlation-ID: f29-06-026442ea96ec4cdb


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f29-06-026442ea96ec4cdb

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

<a id="exchange-12"></a>
## 12. GET /api/engagement/openapi.yaml (version)

`X-Correlation-ID: f29-06-f316f730c1a84d80`, at 2026-11-24T03:59:30Z

```http
GET https://127.0.0.1/api/engagement/openapi.yaml
X-Correlation-ID: f29-06-f316f730c1a84d80


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f29-06-f316f730c1a84d80

openapi: 3.1.0
info:
  title: engagement-service
  version: 2.5.0
  description: 'Records client-originated field events (RN-55): in-store engagement
    events and capture incidents, the device''s reports of a refused queued write
    (ADR-0071). Both live in MongoDB, in `engagement_event` and `capture_incident`.
    Engagement events live in MongoDB, are recorded once per client event, attributed
    to the caller, voided rather than deleted, and summarized. `POST /v1/engagement-events`
    and `POST /v2/engagement-events` read PostgreSQL through the published store,
    product and customer views to check the references each kind carries. Every
    operation answers 503 `database_unavailable` when its database is unreachable,
    MongoDB included. XML events are polymorphic: each event or request root carries
    the `xsi:type` for its kind, so each schema admits only that kind''s fields. `/v1`
    is frozen. `/v2` (XSD `engagement.v2.xsd`, namespace
    `.../xml/engagement/v2`, served at `/schema.v2.xsd`) keeps `QR_SCAN` and `RECO_CLICK`
    with their meaning and adds `STORE_VISIT`, `RECO_IMPRESSION`, `CAMPAIGN_EXPOSURE`,
    `CART_ADD`, `CART_REMOVE` and `NAVIGATION`, each a distinct fact. None of these events
    is a sale: a confirmed transaction exists only in sales-service. A `CAMPAIGN_EXPOSURE`
    carries the campaign and the customer. experimentation does not call this service:
    engagement relays the exposure on the Redis Stream `stream:engagement.v1.interaction_recorded`
    from an outbox kept on the event document (ADR-0075), and experimentation keeps its own
    projection and counts exposures per campaign and customer from it, never as a conversion.
    A void reaches that projection as a later notice, so the count is eventually consistent.
    The stream is contracted in `contracts/streams/engagement.interaction_recorded.v1.md`. The v2 summary
    counts every type.'
security:
- bearer: []
tags:
- name: Engagement
- name: Capture incidents
- name: Meta
paths:
  /v1/engagement-events:
    post:
      tags:
      - Engagement
      summary: Record a QR scan or a recommendation click
      description: 'Records the event once per `client_event_id`, attributed to
        the token''s `sub` (`recorded_by`). The body has two shapes, `QR_SCAN` and
        `RECO_CLICK`. Store, product and customer are checked against the published
        store, product and customer views: the store must exist and be active. A
        new event answers 201. The same `client_event_id` arriving again with the
        same `type`, `store_id` and `product_id` answers 200 with the original event
        and writes nothing; with any other type, store or product it answers 409
        `client_event_conflict`. `occurred_at` more than five minutes ahead of the
        server answers 422 `occurred_in_future`. A caller confined to assigned stores (STORE_MANAGER, SALES_ASSOCIATE) recording for a store that is not assigned to them gets 403 forbidden_store, decided before any reference is checked and with nothing stored (ADR-0060). ADMIN is not confined.'
      operationId: createEngagementEvent
      x-mosaiq-roles:
      - ADMIN
      - STORE_MANAGER
      - SALES_ASSOCIATE
      parameters:
      - $ref: '#/components/parameters/CorrelationId'
      requestBody:
        required: true
        content:
          application/json:
            schema: &id001
              $ref: '#/components/schemas/EngagementEventRequest'
            examples:
              qr_scan:
                externalValue: ./examples/qr-scan.request.json
              reco_click:
                externalValue: ./examples/reco-click.request.json
          application/xml:
            schema: *id001
            examples:
              qr_scan:
                externalValue: ./examples/qr-scan.request.xml
              reco_click:
                externalValue: ./examples/reco-click.request.xml
      responses:
        '200':
          description: 'A replay: the original event, nothing written.'
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema: &id002
                $ref: '#/components/schemas/EngagementEvent'
              examples:
                replay:
                  externalValue: ./examples/replay.json
            application/xml:
              schema: *id002
              examples:
                replay:
                  externalValue: ./examples/replay.xml
        '201':
          description: The event was recorded.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema: &id003
                $ref: '#/components/schemas/EngagementEvent'
              examples:
                qr_scan:
                  externalValue: ./examples/qr-scan.json
                reco_click:
                  externalValue: ./examples/reco-click.json
            application/xml:
              schema: *id003
              examples:
                qr_scan:
                  externalValue: ./examples/qr-scan.xml
                reco_click:
                  externalValue: ./examples/reco-click.xml
        '400':
          $ref: '#/components/responses/MalformedBody'
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/EventForbidden'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '409':
          $ref: '#/components/responses/CreateConflict'
        '413':
          $ref: '#/components/responses/PayloadTooLarge'
        '415':
          $ref: '#/components/responses/UnsupportedMediaType'
        '422':
          $ref: '#/components/responses/CreateInvalid'
        '429':
          $ref: '#/components/responses/RateLimited'
        '503':
          $ref: '#/components/responses/OperationUnavailable
… (90109 more characters)
```

<a id="exchange-13"></a>
## 13. GET /api/check-ins/openapi.yaml (version)

`X-Correlation-ID: f29-06-40ed4646888847c5`, at 2026-11-24T03:59:30Z

```http
GET https://127.0.0.1/api/check-ins/openapi.yaml
X-Correlation-ID: f29-06-40ed4646888847c5


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f29-06-40ed4646888847c5

openapi: 3.1.0
info:
  title: check-ins-service
  version: 1.4.0
  description: 'Records staff GPS check-ins and computes their verdict on the server
    (RN-56). The phone sends a fix and shows the answer; it never decides. Check-ins
    are never updated or deleted, and an override is an append-only review. The
    server decides the verdict, as one rule (RN-56). The distance is the haversine
    distance on a sphere of radius 6,371,008.8 m from the fix to the store''s location
    in `api_stores.store_directory_v1`, in metres with one decimal. The first matching
    reason wins: `no_fix` (no fix sent), `store_location_unknown` (the store has
    no location), `low_accuracy` (`accuracy_m` greater than the store''s `geofence_radius_m`),
    `within_geofence` (distance at most `geofence_radius_m`), otherwise `outside_geofence`.
    Each of the five is a 201 answer, not an error. The effective `verified` is
    the latest review''s verdict when a review exists, else `auto_verified`. Reads
    are limited to ADMIN, STORE_MANAGER and AUDITOR because they are staff location
    data. The service also records authorized customer visits, a separate resource
    (`customer-visits`): a customer fact that names a customer, a store, a time
    and optionally a campaign, and never a position, so staff and customer facts
    never mix (ADR-0035). A visit needs an active loyalty code and the customer''s
    granted `PROFILING` consent (ADR-0066), a repeat within the 2-hour duplicate
    window is the same visit, it is kept 36 months from `occurred_at`, and it is
    read by ADMIN, AUDITOR and, in their assigned stores, STORE_MANAGER and
    SALES_ASSOCIATE. The three customer-visit operations are `x-mosaiq-status:
    planned` (ADR-0073) until F21-23 (#801) implements them.'
security:
- bearer: []
tags:
- name: CheckIns
- name: CustomerVisits
- name: Meta
paths:
  /v1/check-ins:
    post:
      tags:
      - CheckIns
      summary: Record a check-in
      description: 'The server decides the verdict, as one rule (RN-56). The distance
        is the haversine distance on a sphere of radius 6,371,008.8 m from the fix
        to the store''s location in `api_stores.store_directory_v1`, in metres with
        one decimal. The first matching reason wins: `no_fix` (no fix sent), `store_location_unknown`
        (the store has no location), `low_accuracy` (`accuracy_m` greater than the
        store''s `geofence_radius_m`), `within_geofence` (distance at most `geofence_radius_m`),
        otherwise `outside_geofence`. Each of the five is a 201 answer, not an error.
        `auto_verified` is true exactly when the reason is `within_geofence`. The
        user is the token''s `sub`. Replaying a `client_check_in_id` with the same
        user, store, fix and `occurred_at` answers 200 with the original check-in
        and writes nothing; the same id with any other value answers 409 `client_check_in_conflict`.
        A phone retries a request refused with 503 under the same `client_check_in_id`. A caller confined to assigned stores (STORE_MANAGER, SALES_ASSOCIATE, LOYALTY_OFFICER, COMMERCIAL_SUPERVISOR) checking in at a store that is not assigned to them gets 403 forbidden_store (detail store_id, other_store), decided after the replay and the future check and before the store is looked up, with nothing stored (ADR-0060). ADMIN is not confined.'
      operationId: createCheckIn
      x-mosaiq-roles:
      - ADMIN
      - STORE_MANAGER
      - SALES_ASSOCIATE
      - LOYALTY_OFFICER
      - COMMERCIAL_SUPERVISOR
      parameters:
      - $ref: '#/components/parameters/CorrelationId'
      requestBody:
        required: true
        content:
          application/json:
            schema: &id001
              $ref: '#/components/schemas/CheckInRequest'
            examples:
              with_fix:
                externalValue: ./examples/check-in.request.json
              no_fix:
                externalValue: ./examples/check-in-no-fix.request.json
          application/xml:
            schema: *id001
            examples:
              with_fix:
                externalValue: ./examples/check-in.request.xml
              no_fix:
                externalValue: ./examples/check-in-no-fix.request.xml
      responses:
        '200':
          description: 'A replay: the original check-in, nothing written.'
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema: &id002
                $ref: '#/components/schemas/CheckIn'
              examples:
                replay:
                  externalValue: ./examples/check-in-replay.json
            application/xml:
              schema: *id002
              examples:
                replay:
                  externalValue: ./examples/check-in-replay.xml
        '201':
          description: The check-in was recorded.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema: &id003
                $ref: '#/components/schemas/CheckIn'
              examples:
                within_geofence:
                  externalValue: ./examples/check-in-within-geofence.json
                outside_geofence:
                  externalValue: ./examples/check-in-outside-geofence.json
                low_accuracy:
                  externalValue: ./examples/check-in-low-accuracy.json
                store_location_unknown:
                  externalValue: ./examples/check-in-store-location-unknown.json
                no_fix:
                  externalValue: ./examples/check-in-no-fix.json
            application/xml:
              schema: *id003
              examples:
                within_geofence:
                  externalValue: ./examples/check-in-within-geofence.xml
                outside_geofence:
                  externalValue: ./examples
… (51155 more characters)
```

<a id="exchange-14"></a>
## 14. GET /api/campaigns/openapi.yaml (version)

`X-Correlation-ID: f29-06-24e01134e5d84eb0`, at 2026-11-24T03:59:30Z

```http
GET https://127.0.0.1/api/campaigns/openapi.yaml
X-Correlation-ID: f29-06-24e01134e5d84eb0


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f29-06-24e01134e5d84eb0

openapi: 3.1.0
info:
  title: campaigns-service
  version: 1.4.2
  description: >-
    Owns the campaign lifecycle rule. A campaign is created as DRAFT, may be
    edited only while it remains DRAFT, and moves only DRAFT to ACTIVE or
    CANCELLED and ACTIVE to FINISHED or CANCELLED. FINISHED and CANCELLED are
    terminal. Activation is refused after the campaign has ended or while any
    attached experiment is not ready. `label_code` is the stable segment label
    vocabulary key; segment ids are never accepted or returned.
security:
  - bearer: []
paths:
  /v1/campaigns:
    get:
      operationId: listCampaigns
      summary: List campaigns
      description: >-
        Returns campaigns ordered by `campaign_id`. `status` and `label_code`
        are optional filters and pagination applies after filtering.
      x-mosaiq-roles:
        - ADMIN
        - ANALYST
        - MARKETING
        - AUDITOR
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
        - name: status
          in: query
          required: false
          description: Exact lifecycle status to return.
          schema:
            $ref: '#/components/schemas/CampaignStatus'
        - name: label_code
          in: query
          required: false
          description: Exact stable segment label code to return.
          schema:
            $ref: '#/components/schemas/LabelCode'
        - $ref: '#/components/parameters/Page'
        - $ref: '#/components/parameters/Size'
      responses:
        '200':
          description: A page of campaigns.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Campaigns'
              examples:
                campaigns:
                  externalValue: ./examples/campaigns.json
            application/xml:
              schema:
                $ref: '#/components/schemas/Campaigns'
              examples:
                campaigns:
                  externalValue: ./examples/campaigns.xml
        '401':
          $ref: '#/components/responses/AuthenticationError'
        '403':
          $ref: '#/components/responses/ForbiddenRole'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '422':
          $ref: '#/components/responses/ListValidation'
        '429':
          $ref: '#/components/responses/RateLimited'
        '503':
          $ref: '#/components/responses/ProtectedDatabaseUnavailable'
    post:
      operationId: createCampaign
      summary: Create a draft campaign
      description: >-
        Creates a campaign in DRAFT. The server allocates `campaign_id`;
        `campaign_id` and `status` are forbidden in the body and return 422
        `invalid_body`. The end date must not precede the start date, and
        `label_code` must exist in the stable segment label vocabulary. A DRAFT
        campaign with the same name, `label_code`, `starts_on` and `ends_on`
        returns 409 `duplicate_draft`.
      x-mosaiq-roles:
        - ADMIN
        - MARKETING
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/CreateCampaignRequest'
            examples:
              draft:
                externalValue: ./examples/create-campaign.request.json
          application/xml:
            schema:
              $ref: '#/components/schemas/CreateCampaignRequest'
            examples:
              draft:
                externalValue: ./examples/create-campaign.request.xml
      responses:
        '201':
          description: Draft campaign created.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
            Location:
              description: Canonical URI of the new campaign.
              required: true
              schema:
                type: string
                format: uri-reference
                example: /v1/campaigns/301
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Campaign'
              examples:
                draft:
                  externalValue: ./examples/campaign.json
            application/xml:
              schema:
                $ref: '#/components/schemas/Campaign'
              examples:
                draft:
                  externalValue: ./examples/campaign.xml
        '400':
          $ref: '#/components/responses/MalformedBody'
        '401':
          $ref: '#/components/responses/AuthenticationError'
        '403':
          $ref: '#/components/responses/ForbiddenRole'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '409':
          $ref: '#/components/responses/CampaignCreateConflict'
        '413':
          $ref: '#/components/responses/PayloadTooLarge'
        '415':
          $ref: '#/components/responses/UnsupportedMediaType'
        '422':
          $ref: '#/components/responses/CampaignValidation'
        '429':
          $ref: '#/components/responses/RateLimited'
        '503':
          $ref: '#/components/responses/ProtectedDatabaseUnavailable'
  /v1/campaigns/{campaign_id}:
    get:
      operationId: getCampaign
      summary: Get a campaign
      description: Returns one campaign and the transitions currently allowed.
      x-mosaiq-roles:
        - ADMIN
        - ANALYST
        - MARKETING
        - AUDITOR
      parameters:
        - $ref: '#/components/parameters/CampaignId'
        - $ref: '#/components/parameters/CorrelationId'
      responses:
        '200':
          description: The campaign.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
         
… (87247 more characters)
```

<a id="exchange-15"></a>
## 15. GET /api/audit-trail/openapi.yaml (version)

`X-Correlation-ID: f29-06-45305eadd94c4281`, at 2026-11-24T03:59:30Z

```http
GET https://127.0.0.1/api/audit-trail/openapi.yaml
X-Correlation-ID: f29-06-45305eadd94c4281


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f29-06-45305eadd94c4281

openapi: 3.1.0
info:
  title: audit-trail-service
  version: 1.5.0
  description: >-
    Reads the audit trail: the append-only entries `fn_audit` writes for every
    insert, update and delete on an audited table (RN-28, RN-30). An entry is
    attributed to the actor the writer's connection identified in
    `mosaiq.user_id`; `user_id` is null when none was known, as for the seed or
    a maintenance script. An entry never carries a credential: `password_hash`
    is stripped from both payloads before the entry is written, and the service
    strips it again (RN-29). The service has no write operation, because
    `fn_audit` is the only writer of an entry (ADR-0035). Only ADMIN and
    AUDITOR read it.


    It also reports domain events (ADR-0072): calculations, model
    publications and rollbacks, permission changes, critical actions, and
    logged reads, recorded by their producers through
    `api_audit.record_event_v1` into an append-only, hash-chained table under
    the same discipline. They are a separate resource, `domain-events`, so
    the two never mix. Every read of either resource by ADMIN or AUDITOR is
    itself recorded as an `AUDIT_READ` event.
security:
  - bearer: []
paths:
  /v1/audit-entries:
    get:
      summary: List audit entries
      description: >-
        Returns the entries that match every filter given, ordered by
        `executed_at` and then `audit_id`, both descending, so the newest entry
        is first and entries written in one transaction (which share an
        `executed_at`) keep a stable order. Pagination applies after
        filtering. `entity` is one of the audited table names and `action` one
        of INSERT, UPDATE and DELETE; an unknown value of either, or a `to`
        before `from`, is 422 `invalid_query` and `details` names the rejected
        parameter (`entity`, `action` or `to`). A `user_id` that is not a UUID,
        or a `from` or `to` that is not an RFC 3339 timestamp with an offset,
        is also 422 `invalid_query`, with `details` code `invalid_format`; an
        `entity_pk` without `entity` is 422 `invalid_query` with `details` code
        `requires_entity`, because one key value can name rows of several
        tables (#533). A successful call records one `AUDIT_READ` domain
        event naming the filters used, never their values; when it cannot, the
        call is 503 `audit_unavailable` and returns no entries (ADR-0072).
      operationId: listAuditEntries
      x-mosaiq-roles:
        - ADMIN
        - AUDITOR
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
        - $ref: '#/components/parameters/Entity'
        - $ref: '#/components/parameters/EntityPk'
        - $ref: '#/components/parameters/UserId'
        - $ref: '#/components/parameters/Action'
        - $ref: '#/components/parameters/From'
        - $ref: '#/components/parameters/To'
        - $ref: '#/components/parameters/Page'
        - $ref: '#/components/parameters/Size'
      responses:
        '200':
          description: A page of audit entries, newest first.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/AuditEntries'
              examples:
                page:
                  externalValue: ./examples/audit-entries.json
            application/xml:
              schema:
                $ref: '#/components/schemas/AuditEntries'
              examples:
                page:
                  externalValue: ./examples/audit-entries.xml
        '401':
          $ref: '#/components/responses/AuthenticationError'
        '403':
          $ref: '#/components/responses/ForbiddenRole'
        '406':
          $ref: '#/components/responses/NotAcceptable'
        '422':
          $ref: '#/components/responses/AuditQueryValidation'
        '429':
          $ref: '#/components/responses/RateLimited'
        '503':
          $ref: '#/components/responses/ProtectedDatabaseUnavailable'
  /v1/audit-entries/{audit_id}:
    get:
      summary: Get one audit entry
      description: >-
        Returns the entry identified by `audit_id`, with its payloads. An entry
        is never changed once written, so the representation of an existing
        `audit_id` is stable. An unknown `audit_id` is 404
        `audit_entry_not_found`. A path segment that is not a positive integer
        matches no route and is the kit's 404 `not_found` (#533). A
        successful call records one `AUDIT_READ` domain event, and is 503
        `audit_unavailable` when it cannot (ADR-0072).
      operationId: getAuditEntry
      x-mosaiq-roles:
        - ADMIN
        - AUDITOR
      parameters:
        - $ref: '#/components/parameters/AuditId'
        - $ref: '#/components/parameters/CorrelationId'
      responses:
        '200':
          description: The requested audit entry.
          headers:
            X-Correlation-ID:
              $ref: '#/components/headers/CorrelationId'
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/AuditEntry'
              examples:
                insert:
                  externalValue: ./examples/audit-entry-insert.json
                update:
                  externalValue: ./examples/audit-entry-update.json
                delete:
                  externalValue: ./examples/audit-entry-delete.json
                app_user_update:
                  externalValue: ./examples/audit-entry-app-user-update.json
            application/xml:
              schema:
                $ref: '#/components/schemas/AuditEntry'
              examples:
                insert:
                  externalValue: ./examples/audit-entry-insert.xml
                update:
                  externalValue: ./examples/audit-entry-update.xml
                delete:
                  externalValue: ./examp
… (48449 more characters)
```

<a id="exchange-16"></a>
## 16. GET /api/rfm-engine/openapi.yaml (version)

`X-Correlation-ID: f29-06-eff9824e9c104834`, at 2026-11-24T03:59:30Z

```http
GET https://127.0.0.1/api/rfm-engine/openapi.yaml
X-Correlation-ID: f29-06-eff9824e9c104834


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f29-06-eff9824e9c104834

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

<a id="exchange-17"></a>
## 17. GET /api/model-registry/openapi.yaml (version)

`X-Correlation-ID: f29-06-021403a395b24b85`, at 2026-11-24T03:59:30Z

```http
GET https://127.0.0.1/api/model-registry/openapi.yaml
X-Correlation-ID: f29-06-021403a395b24b85


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f29-06-021403a395b24b85

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

<a id="exchange-18"></a>
## 18. GET /api/experimentation/openapi.yaml (version)

`X-Correlation-ID: f29-06-9dd571299aba43ef`, at 2026-11-24T03:59:30Z

```http
GET https://127.0.0.1/api/experimentation/openapi.yaml
X-Correlation-ID: f29-06-9dd571299aba43ef


```

```http
HTTP 200
Content-Type: application/yaml
X-Correlation-ID: f29-06-9dd571299aba43ef

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

<a id="exchange-19"></a>
## 19. GET /api/analytics-kpis/openapi.yaml (version)

`X-Correlation-ID: f29-06-24bee00ba9d54d7b`, at 2026-11-24T03:59:30Z

```http
GET https://127.0.0.1/api/analytics-kpis/openapi.yaml
X-Correlation-ID: f29-06-24bee00ba9d54d7b


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f29-06-24bee00ba9d54d7b

openapi: 3.1.0
info:
  title: analytics-kpis-service
  version: 1.2.0
  description: >-
    Reports the commercial KPIs of the platform under their published
    definition, window and data origin, and only to the roles and stores
    allowed to read them (RN-68). The service is read-only: it has no write
    operation and owns no table. It reads only `api_*` views published by the
    other boundaries (ADR-0044) and never a base table, and it calls no other
    service.


    **One definition per figure.** `GET /v1/kpi-reports/definitions` publishes,
    for each of the seven KPIs, its definition, window, data origin, dimensions
    and measures. Every report names its `kpi` and `definition_version`, and
    every measure has a `code`, a `kind` (`OBSERVED`, `ATTRIBUTED` or
    `INCREMENTAL`) and a `unit`, so a figure cannot mean two things. A change of
    definition raises `definition_version` and is announced here before it is
    served.


    **Windows.** `from` and `to` are dates read in UTC; `to` is inclusive.
    A report is computed as of its `generated_at`, so a reversal recorded after
    the sale is subtracted from it.


    **Segments.** The `segment` of a sales-based KPI is the label the customer
    holds at `generated_at`, from `api_segments.current_label_v1`; a customer
    with no label is reported as it is, with `segment_state`
    `unassigned_result` or `no_open_assignment` and no `segment_code` (RN-21).
    `data_origin.spans_model_versions` is true, and `model_version_ids` names
    them, when the window holds a change of model version, so a figure that
    compares periods across a version is marked as such (ADR-0064).


    **Provenance.** A row has `synthetic` true when any sale or run it
    aggregates is `SEEDED`, and the report's `data_origin.synthetic` is true
    when any row is (ADR-0052). A client shows such a figure as Synthetic.


    **Store scope (ADR-0060).** `STORE_MANAGER` and `COMMERCIAL_SUPERVISOR` read
    only the stores assigned to them, read from `api_users.store_assignment_v1`
    on each request. Naming another store is 403 `forbidden_store`. With no
    store named, the report covers the assigned stores only, `store_scope`
    says so, and a role with no assignment gets an empty report. A KPI whose
    figure has no store (`migrations`, `campaign-performance`) is not
    readable by these roles. Every other role reads all stores. No report
    carries a customer identifier.


    **Producer views.** Consumed and published: `api_segments.current_label_v1`,
    `api_segments.assignment_change_v1` (one row per change of a customer's label
    between two consecutive assignments: customer, previous and new label code,
    previous and new model version, instant, run and data origin) and
    `api_models.run_version_v1`, which names the model version behind a label.
    Consumed and contracted by their producers' open contracts:
    `api_sales.net_purchase_line_v1` (which must also publish a `data_origin`
    column for ADR-0052) and `api_users.store_assignment_v1`. Required of their
    producers and not yet contracted: `api_recommendations.interaction_v1` (one row per `RECO_IMPRESSION` and
    `RECO_CLICK` that is not voided: type, customer, store, instant, data origin),
    `api_experiments.exposure_v1` (one row per `CAMPAIGN_EXPOSURE` that is not
    voided: campaign, customer, store, instant, data origin),
    and `api_recommendations.recommended_purchase_v1` (one row per sale line bought
    through a recommendation: sale, store, channel, instant).
    This contract invents none of their columns; each producer's story fixes
    them, and the reports that read them stay planned until they exist.
    Consumed and contracted by experimentation: `api_experiments.campaign_effect_v1`
    (one row per measured experiment of a campaign: its window, arm sizes,
    attributed amount, effect, interval and the interval's confidence, data
    origin), read by `getCampaignPerformanceReport`.


    `getDemandCoverageReport` also consumes two published views, contracted
    by their producers: `api_stock.stock_level_v1` and
    `api_products.product_ref_v1`. The story that implements it adds their
    grant rows for `svc_kpis` and names analytics-kpis as their consumer.


    **Implemented so far.** The stand-up story (F26-02) serves `getNetSalesReport`
    and `listKpiDefinitions`, F26-05 serves `getDemandCoverageReport`, and F26-03
    serves `getSpendReport`, `getRetentionReport` and `getMigrationReport`. Every other
    report is marked `x-mosaiq-status: planned` (ADR-0073) until its own story removes
    the marker.
security:
  - bearer: []
paths:
  /v1/kpi-reports/net-sales:
    get:
      summary: Report net sales
      description: >-
        Net sales (`net-sales`, definition version 1): the net amount, net units and net orders of the sales that still stand, grouped by any of `segment`, `store`, `channel` and `period`. A sale counts at the instant it happened and reversals are subtracted (ADR-0062), so the figure reconciles with the web's `/reports/`. Origin: `api_sales.net_purchase_line_v1`, and `api_segments.current_label_v1` for `segment`. A row is `synthetic` when any sale it aggregates is `SEEDED` (ADR-0052).
      operationId: getNetSalesReport
      x-mosaiq-roles:
        - ADMIN
        - ANALYST
        - MARKETING
        - AUDITOR
        - STORE_MANAGER
        - COMMERCIAL_SUPERVISOR
      parameters:
        - $ref: '#/components/parameters/CorrelationId'
        - $ref: '#/components/parameters/From'
        - $ref: '#/components/parameters/To'
        - $ref: '#/components/parameters/GroupBy'
        - $ref: '#/components/parameters/Granularity'
        - $ref: '#/components/parameters/StoreId'
        - $ref: '#/components/parameters/SegmentCode'
        - $ref: '#/components/parameters/ChannelId'
        - $ref: '#/components/parameters/Page'
        - $ref: '#/components/parameters/Size'
      responses:
        '200':
        
… (53961 more characters)
```
