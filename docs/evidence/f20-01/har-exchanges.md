### 1. `POST /api/auth/v1/sessions`

Request:

```http
POST /api/auth/v1/sessions HTTP/2.0
accept: application/json
x-correlation-id: 456ec2e3-dfdc-4eaf-836e-257fc6facb79
content-type: application/json; charset=utf-8
content-length: 135
accept-encoding: gzip
user-agent: okhttp/4.12.0

{
  "email": "f20-01-store-manager@example.test",
  "password": "<masked #1>",
  "device_label": "Android Google sdk_gphone64_x86_64"
}
```

Response:

```http
HTTP/2.0 201
date: Tue, 24 Nov 2026 03:41:30 GMT
content-type: application/json
content-length: 881
server: cloudflare
x-correlation-id: 456ec2e3-dfdc-4eaf-836e-257fc6facb79
strict-transport-security: max-age=31536000; includeSubDomains
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: strict-origin-when-cross-origin
content-security-policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
permissions-policy: camera=(), microphone=(), geolocation=(), payment=()
cf-cache-status: DYNAMIC
report-to: {"group":"cf-nel","max_age":604800,"endpoints":[{"url":"https://a.nel.cloudflare.com/report/v4?s=RMOc%2FAhLoNdNHy1MF0tySINA44LVDEV5AGFhNUC0Jfs1C2QtE82lMlszvJs9q8ibyXALKTHtG8eeDKoTOyt7GaCz4t6wIGAHe31MOZmkZkbHU9DMOc%2FkaleSf9bqptA4C3zW0U%2B6AGnp1NSP"}]}
nel: {"report_to":"cf-nel","success_fraction":0.0,"max_age":604800}
cf-ray: a5f565f8b802be38-DFW
alt-svc: h3=":443"; ma=86400

{
  "access_token": "<masked #2>",
  "refresh_token": "<masked #3>",
  "token_type": "Bearer",
  "expires_in": 600,
  "expires_at": "2026-11-24T03:51:30Z"
}
```

### 2. `GET /api/users/v1/users/me`

Request:

```http
GET /api/users/v1/users/me HTTP/2.0
accept: application/json
x-correlation-id: 3bb5c189-56d5-4b13-aaa1-a9cebb23d4ee
authorization: Bearer <masked #2>
accept-encoding: gzip
user-agent: okhttp/4.12.0
```

Response:

```http
HTTP/2.0 200
date: Tue, 24 Nov 2026 03:41:31 GMT
content-type: application/json
server: cloudflare
x-correlation-id: 3bb5c189-56d5-4b13-aaa1-a9cebb23d4ee
strict-transport-security: max-age=31536000; includeSubDomains
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: strict-origin-when-cross-origin
content-security-policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
permissions-policy: camera=(), microphone=(), geolocation=(), payment=()
cf-cache-status: DYNAMIC
report-to: {"group":"cf-nel","max_age":604800,"endpoints":[{"url":"https://a.nel.cloudflare.com/report/v4?s=x8NdTGBZ9vZFaQ%2B4M6yd00fh7vAbv5DilLh3W9bVL1yJ3a70WuySPHYPPn1hL4PBUcdFdXj1%2FL0sp3XZ2G9Cnnqet8x9ccoCletKALVMOlOA3S%2FguS14zAQGbV3brLgbfFN3YZH3Rrt4mSOd"}]}
nel: {"report_to":"cf-nel","success_fraction":0.0,"max_age":604800}
content-encoding: gzip
cf-ray: a5f565fccee1be38-DFW
alt-svc: h3=":443"; ma=86400

{
  "user_id": "927d1bf9-c8cb-43ca-9841-704e5d0993bb",
  "name": "F20-01 Store Manager",
  "email": "f20-01-store-manager@example.test",
  "role_code": "STORE_MANAGER",
  "is_active": true,
  "created_at": "2026-11-24T03:39:01.083764Z"
}
```

### 3. `GET /api/sales/v1/channels`

Request:

```http
GET /api/sales/v1/channels HTTP/2.0
accept: application/json
x-correlation-id: d4cc2471-0291-44a2-a7cd-ff9c3a63a9f1
authorization: Bearer <masked #2>
accept-encoding: gzip
user-agent: okhttp/4.12.0
```

Response:

```http
HTTP/2.0 200
date: Tue, 24 Nov 2026 03:42:30 GMT
content-type: application/json
server: cloudflare
x-correlation-id: d4cc2471-0291-44a2-a7cd-ff9c3a63a9f1
strict-transport-security: max-age=31536000; includeSubDomains
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: strict-origin-when-cross-origin
content-security-policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
permissions-policy: camera=(), microphone=(), geolocation=(), payment=()
cf-cache-status: DYNAMIC
report-to: {"group":"cf-nel","max_age":604800,"endpoints":[{"url":"https://a.nel.cloudflare.com/report/v4?s=9lBj8UoNKcfs3o6WetKx9It5aT2rLirJhkUKWmkbEzW18IT3bdE67TXb8w6LA3QUQhK6uUvzpd6yx2qYiH0hoKN%2Bm2ihEy0gEplW9LA9bFtCcY25U3%2B0sSNBqL6rSnG%2BzMIe2wAyNK%2F%2B%2Fxc5"}]}
nel: {"report_to":"cf-nel","success_fraction":0.0,"max_age":604800}
content-encoding: gzip
cf-ray: a5f567724ef4be38-DFW
alt-svc: h3=":443"; ma=86400

{
  "channels": [
    {
      "channel_id": 1,
      "name": "mobile_app"
    },
    {
      "channel_id": 2,
      "name": "web"
    },
    {
      "channel_id": 3,
      "name": "physical_store"
    },
    {
      "channel_id": 4,
      "name": "marketplace"
    },
    {
      "channel_id": 5,
      "name": "call_center"
    }
  ]
}
```

### 4. `GET /api/sales/v1/channels`

Request:

```http
GET /api/sales/v1/channels HTTP/2.0
accept: application/json
x-correlation-id: fc1dd8bf-087a-4cd9-b405-a688a6a68a45
authorization: Bearer <masked #2>
accept-encoding: gzip
user-agent: okhttp/4.12.0
```

Response:

```http
HTTP/2.0 200
date: Tue, 24 Nov 2026 03:44:00 GMT
content-type: application/json
server: cloudflare
x-correlation-id: fc1dd8bf-087a-4cd9-b405-a688a6a68a45
strict-transport-security: max-age=31536000; includeSubDomains
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: strict-origin-when-cross-origin
content-security-policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
permissions-policy: camera=(), microphone=(), geolocation=(), payment=()
cf-cache-status: DYNAMIC
report-to: {"group":"cf-nel","max_age":604800,"endpoints":[{"url":"https://a.nel.cloudflare.com/report/v4?s=8IgEq8Cvb1VfeDYNPU0yLo%2FW5mu0SB2%2F2PhBuOa3okQG%2BnEX02i%2FjlfbuhTE1ldLXG%2BA4mall17sByaIQ3j8mPSopaRb9hXtHEYj%2B%2Fq3y62%2FGdfAAju88aOaH2WQTOJCZCCsndA2Xsa4Lrxd"}]}
nel: {"report_to":"cf-nel","success_fraction":0.0,"max_age":604800}
content-encoding: gzip
cf-ray: a5f569a52c6ebe38-DFW
alt-svc: h3=":443"; ma=86400

{
  "channels": [
    {
      "channel_id": 1,
      "name": "mobile_app"
    },
    {
      "channel_id": 2,
      "name": "web"
    },
    {
      "channel_id": 3,
      "name": "physical_store"
    },
    {
      "channel_id": 4,
      "name": "marketplace"
    },
    {
      "channel_id": 5,
      "name": "call_center"
    }
  ]
}
```

### 5. `POST /api/sales/v1/sales`

Request:

```http
POST /api/sales/v1/sales HTTP/2.0
idempotency-key: 13c5221e-87e0-450e-af84-2ec564635adc
accept: application/json
x-correlation-id: 24b2737f-937b-4955-b6f3-1a472a6a4a5f
authorization: Bearer <masked #2>
content-type: application/json; charset=utf-8
content-length: 244
accept-encoding: gzip
user-agent: okhttp/4.12.0

{
  "customer_id": "00000000-0000-0000-0000-000000000028",
  "store_id": 30,
  "channel_id": 3,
  "occurred_at": "2026-11-23T21:48:25.968504-06:00",
  "lines": [
    {
      "product_id": 1,
      "quantity": 1,
      "unit_price": "18.75"
    },
    {
      "product_id": 2,
      "quantity": 1,
      "unit_price": "22.50"
    }
  ]
}
```

Response:

```http
HTTP/2.0 422
date: Tue, 24 Nov 2026 03:48:25 GMT
content-type: application/json
content-length: 176
server: cloudflare
x-correlation-id: 24b2737f-937b-4955-b6f3-1a472a6a4a5f
strict-transport-security: max-age=31536000; includeSubDomains
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: strict-origin-when-cross-origin
content-security-policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
permissions-policy: camera=(), microphone=(), geolocation=(), payment=()
cf-cache-status: DYNAMIC
report-to: {"group":"cf-nel","max_age":604800,"endpoints":[{"url":"https://a.nel.cloudflare.com/report/v4?s=uAnB8SChXNg7rvF3j7t0%2BOdoWerlf5Rz2w3xylvWIx%2FQZ%2FC35JMZkBSdJOZRgmJ6dqydVlrKmPi4URc%2FpKWTvRKvPEEYVb5dNThktqhVA3wdxnfvrG4s09lALdIKvvvTeWzCV6hez%2FMIFNwA"}]}
nel: {"report_to":"cf-nel","success_fraction":0.0,"max_age":604800}
cf-ray: a5f5701b4ccabe38-DFW
alt-svc: h3=":443"; ma=86400

{
  "code": "future_dated",
  "message": "The sale is dated in the future.",
  "correlation_id": "24b2737f-937b-4955-b6f3-1a472a6a4a5f",
  "details": [
    {
      "field": "occurred_at",
      "code": "future"
    }
  ]
}
```

### 6. `POST /api/sales/v1/sales`

Request:

```http
POST /api/sales/v1/sales HTTP/2.0
idempotency-key: 73722300-0f88-459c-afd1-0d624ec27561
accept: application/json
x-correlation-id: 8f4a7979-8278-49b5-a40b-6f7becedda19
authorization: Bearer <masked #2>
content-type: application/json; charset=utf-8
content-length: 244
accept-encoding: gzip
user-agent: okhttp/4.12.0

{
  "customer_id": "00000000-0000-0000-0000-000000000028",
  "store_id": 30,
  "channel_id": 3,
  "occurred_at": "2026-11-23T21:50:27.967196-06:00",
  "lines": [
    {
      "product_id": 1,
      "quantity": 1,
      "unit_price": "18.75"
    },
    {
      "product_id": 2,
      "quantity": 1,
      "unit_price": "22.50"
    }
  ]
}
```

Response:

```http
HTTP/2.0 422
date: Tue, 24 Nov 2026 03:50:27 GMT
content-type: application/json
content-length: 176
server: cloudflare
x-correlation-id: 8f4a7979-8278-49b5-a40b-6f7becedda19
strict-transport-security: max-age=31536000; includeSubDomains
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: strict-origin-when-cross-origin
content-security-policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
permissions-policy: camera=(), microphone=(), geolocation=(), payment=()
cf-cache-status: DYNAMIC
report-to: {"group":"cf-nel","max_age":604800,"endpoints":[{"url":"https://a.nel.cloudflare.com/report/v4?s=ZMj1J2Y09dOjRw6a1tLcFaiDW6sD70ybDJT0dRN8OHNpwcgnDLx8S6XASoH%2FAnP%2B%2FSqqXuK58CqiGy9QtU4SBQguF1KpwS6K16F7SDEqg9FreJgP8e3HPHbviR3vgu%2FQpXm%2FNZNLO%2BcPE0ip"}]}
nel: {"report_to":"cf-nel","success_fraction":0.0,"max_age":604800}
cf-ray: a5f57315ec82be38-DFW
alt-svc: h3=":443"; ma=86400

{
  "code": "future_dated",
  "message": "The sale is dated in the future.",
  "correlation_id": "8f4a7979-8278-49b5-a40b-6f7becedda19",
  "details": [
    {
      "field": "occurred_at",
      "code": "future"
    }
  ]
}
```

### 7. `POST /api/sales/v1/sales`

Request:

```http
POST /api/sales/v1/sales HTTP/2.0
idempotency-key: 46f97341-a607-4d8c-b3e4-244b02fc408f
accept: application/json
x-correlation-id: 282ac864-cde5-4a36-addd-dff0b97463e7
authorization: Bearer <masked #2>
content-type: application/json; charset=utf-8
content-length: 244
accept-encoding: gzip
user-agent: okhttp/4.12.0

{
  "customer_id": "00000000-0000-0000-0000-000000000028",
  "store_id": 30,
  "channel_id": 3,
  "occurred_at": "2026-11-23T21:51:10.148779-06:00",
  "lines": [
    {
      "product_id": 1,
      "quantity": 1,
      "unit_price": "18.75"
    },
    {
      "product_id": 2,
      "quantity": 1,
      "unit_price": "22.50"
    }
  ]
}
```

Response:

```http
HTTP/2.0 201
date: Tue, 24 Nov 2026 03:51:17 GMT
content-type: application/json
content-length: 269
server: cloudflare
location: /v1/sales/307
x-correlation-id: 282ac864-cde5-4a36-addd-dff0b97463e7
strict-transport-security: max-age=31536000; includeSubDomains
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: strict-origin-when-cross-origin
content-security-policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
permissions-policy: camera=(), microphone=(), geolocation=(), payment=()
cf-cache-status: DYNAMIC
report-to: {"group":"cf-nel","max_age":604800,"endpoints":[{"url":"https://a.nel.cloudflare.com/report/v4?s=GvDEN2ve9rFIzSfDNf9agpfY8xOomvfA9isYhp%2F7EOQOLTHCP3PDi8p%2FiQOShVZ%2FIF3ZoCzi8vQvKBQWJS3dUn6F6zBu8kHM%2F6z2fH%2FguR3vI%2BTErxZT64lJjJMshhAuTzVlYf2MBCBriJLG"}]}
nel: {"report_to":"cf-nel","success_fraction":0.0,"max_age":604800}
cf-ray: a5f5744f5e5bbe38-DFW
alt-svc: h3=":443"; ma=86400

{
  "sale_id": 307,
  "customer_id": "00000000-0000-0000-0000-000000000028",
  "store_id": 30,
  "channel_id": 3,
  "occurred_at": "2026-11-24T03:51:10.148779Z",
  "total": "41.25",
  "lines": [
    {
      "product_id": 1,
      "quantity": 1,
      "unit_price": "18.75"
    },
    {
      "product_id": 2,
      "quantity": 1,
      "unit_price": "22.50"
    }
  ]
}
```

### 8. `POST /api/auth/v1/sessions/refresh`

Request:

```http
POST /api/auth/v1/sessions/refresh HTTP/2.0
accept: application/json
x-correlation-id: a6861de1-9a47-47dd-bd9b-5c35780da764
content-type: application/json; charset=utf-8
content-length: 63
accept-encoding: gzip
user-agent: okhttp/4.12.0

{
  "refresh_token": "<masked #3>"
}
```

Response:

```http
HTTP/2.0 200
date: Tue, 24 Nov 2026 03:51:34 GMT
content-type: application/json
server: cloudflare
x-correlation-id: a6861de1-9a47-47dd-bd9b-5c35780da764
strict-transport-security: max-age=31536000; includeSubDomains
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: strict-origin-when-cross-origin
content-security-policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
permissions-policy: camera=(), microphone=(), geolocation=(), payment=()
cf-cache-status: DYNAMIC
report-to: {"group":"cf-nel","max_age":604800,"endpoints":[{"url":"https://a.nel.cloudflare.com/report/v4?s=cT%2FRWfQyT7o460DvEjUy4yrc3HPTcTKuWaCPGMVmOsfT5u1wX%2FR8b6hWqpZAJMMKfw9rU71wj5cW%2BENX2LayLDGmifpJdf1azmqaMpHADmjK6iaaen46gcy5gA2dfsdVj4kVQZ57%2Bt3Rw4tg"}]}
nel: {"report_to":"cf-nel","success_fraction":0.0,"max_age":604800}
content-encoding: gzip
cf-ray: a5f574b71e59be38-DFW
alt-svc: h3=":443"; ma=86400

{
  "access_token": "<masked #4>",
  "refresh_token": "<masked #5>",
  "token_type": "Bearer",
  "expires_in": 600,
  "expires_at": "2026-11-24T04:01:34Z"
}
```

