### 1. `POST /api/sales/v1/sales`

Request:

```http
POST /api/sales/v1/sales HTTP/2.0
idempotency-key: 0522a5de-2a7e-47ba-aa85-a8b303c04545
accept: application/json
x-correlation-id: 30223c83-5817-4e43-8b97-18e2b8451d31
authorization: Bearer <masked #1>
content-type: application/json; charset=utf-8
content-length: 193
accept-encoding: gzip
user-agent: okhttp/4.12.0

{
  "customer_id": "00000000-0000-0000-0000-000000000019",
  "store_id": 20,
  "channel_id": 3,
  "occurred_at": "2026-11-23T23:05:43.714701-06:00",
  "lines": [
    {
      "product_id": 1,
      "quantity": 1,
      "unit_price": "18.75"
    }
  ]
}
```

Response:

```http
HTTP/2.0 201
date: Tue, 24 Nov 2026 05:10:40 GMT
content-type: application/json
content-length: 218
server: cloudflare
location: /v1/sales/309
x-correlation-id: 30223c83-5817-4e43-8b97-18e2b8451d31
strict-transport-security: max-age=31536000; includeSubDomains
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: strict-origin-when-cross-origin
content-security-policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
permissions-policy: camera=(), microphone=(), geolocation=(), payment=()
cf-cache-status: DYNAMIC
report-to: {"group":"cf-nel","max_age":604800,"endpoints":[{"url":"https://a.nel.cloudflare.com/report/v4?s=LbRQAlpWN12MtHx6VnGdimsiBP5ncEY6T1JvC3olp9X1%2Bw3TFR23mZYvZrM2RH7yj9zHapqkbUTHQ0smvj2D0GpT7necDmp%2Bgt9MtCTjNGUpSVt4a8csMGkzzoTbXMZnuzoywkpj5rMOoV4E"}]}
nel: {"report_to":"cf-nel","success_fraction":0.0,"max_age":604800}
cf-ray: a5f5e89559f6f0a1-DFW
alt-svc: h3=":443"; ma=86400

{
  "sale_id": 309,
  "customer_id": "00000000-0000-0000-0000-000000000019",
  "store_id": 20,
  "channel_id": 3,
  "occurred_at": "2026-11-24T05:05:43.714701Z",
  "total": "18.75",
  "lines": [
    {
      "product_id": 1,
      "quantity": 1,
      "unit_price": "18.75"
    }
  ]
}
```

### 2. `POST /api/sales/v1/sales`

Request:

```http
POST /api/sales/v1/sales HTTP/2.0
idempotency-key: 0522a5de-2a7e-47ba-aa85-a8b303c04545
accept: application/json
x-correlation-id: 77b5975d-bc79-49f2-80ac-a3eff5b2ceb1
authorization: Bearer <masked #1>
content-type: application/json; charset=utf-8
content-length: 193
accept-encoding: gzip
user-agent: okhttp/4.12.0

{
  "customer_id": "00000000-0000-0000-0000-000000000019",
  "store_id": 20,
  "channel_id": 3,
  "occurred_at": "2026-11-23T23:05:43.714701-06:00",
  "lines": [
    {
      "product_id": 1,
      "quantity": 1,
      "unit_price": "18.75"
    }
  ]
}
```

Response:

```http
HTTP/2.0 200
date: Tue, 24 Nov 2026 05:10:40 GMT
content-type: application/json
server: cloudflare
idempotent-replayed: true
x-correlation-id: 77b5975d-bc79-49f2-80ac-a3eff5b2ceb1
strict-transport-security: max-age=31536000; includeSubDomains
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: strict-origin-when-cross-origin
content-security-policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
permissions-policy: camera=(), microphone=(), geolocation=(), payment=()
cf-cache-status: DYNAMIC
report-to: {"group":"cf-nel","max_age":604800,"endpoints":[{"url":"https://a.nel.cloudflare.com/report/v4?s=99kQwoh7nSEHD%2FptOqqQF1vEW8hm5k4KT3c%2B%2FIl1Ovo0h4P%2B5sA6Ph3sYCvRjO6X%2FlLXjzj6wyobHLIf%2BE6IGkurdNCB2LEvf7XAX8loBzAexZCNtGn3iMeetIS2P1hBVqK2i6O7rJpqxtty"}]}
nel: {"report_to":"cf-nel","success_fraction":0.0,"max_age":604800}
content-encoding: gzip
cf-ray: a5f5e8974faa6a61-DFW
alt-svc: h3=":443"; ma=86400

{
  "sale_id": 309,
  "customer_id": "00000000-0000-0000-0000-000000000019",
  "store_id": 20,
  "channel_id": 3,
  "occurred_at": "2026-11-24T05:05:43.714701Z",
  "total": "18.75",
  "lines": [
    {
      "product_id": 1,
      "quantity": 1,
      "unit_price": "18.75"
    }
  ]
}
```

### 3. `POST /api/sales/v1/sales`

Request:

```http
POST /api/sales/v1/sales HTTP/2.0
idempotency-key: 77c8b2fa-add5-43c1-bb7a-05d84c08b1f6
accept: application/json
x-correlation-id: 24d6aec8-8e81-4d25-bc26-8b7f0b2edc52
authorization: Bearer <masked #1>
content-type: application/json; charset=utf-8
content-length: 193
accept-encoding: gzip
user-agent: okhttp/4.12.0

{
  "customer_id": "00000000-0000-0000-0000-000000000019",
  "store_id": 20,
  "channel_id": 3,
  "occurred_at": "2026-11-23T23:08:13.269610-06:00",
  "lines": [
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
date: Tue, 24 Nov 2026 05:10:40 GMT
content-type: application/json
content-length: 218
server: cloudflare
location: /v1/sales/310
x-correlation-id: 24d6aec8-8e81-4d25-bc26-8b7f0b2edc52
strict-transport-security: max-age=31536000; includeSubDomains
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: strict-origin-when-cross-origin
content-security-policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
permissions-policy: camera=(), microphone=(), geolocation=(), payment=()
cf-cache-status: DYNAMIC
report-to: {"group":"cf-nel","max_age":604800,"endpoints":[{"url":"https://a.nel.cloudflare.com/report/v4?s=ZaSZsv5p9lvAiRtO9EYM3ZP1le4xcoaY1jqp1Hwff4IKV%2BkccMXBN%2BraYiV8%2FOQRLvi%2FZtCyB4rJTFQyHH5KIzFQqZroM5vEMCSCIFN6D75OatNHnObPRWxtccF4BSCM8CMyy%2FqHVyd73M6W"}]}
nel: {"report_to":"cf-nel","success_fraction":0.0,"max_age":604800}
cf-ray: a5f5e898ea996a61-DFW
alt-svc: h3=":443"; ma=86400

{
  "sale_id": 310,
  "customer_id": "00000000-0000-0000-0000-000000000019",
  "store_id": 20,
  "channel_id": 3,
  "occurred_at": "2026-11-24T05:08:13.269610Z",
  "total": "22.50",
  "lines": [
    {
      "product_id": 2,
      "quantity": 1,
      "unit_price": "22.50"
    }
  ]
}
```

