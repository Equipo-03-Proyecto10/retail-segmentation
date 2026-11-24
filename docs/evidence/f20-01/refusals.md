Against https://mosaiq.maxthecoder.online:

- GET /api/users/v1/users with a token: expected 403 forbidden_role, got 403 forbidden_role (held)
- POST /api/sales/v1/sales without a token: expected 401 invalid_token, got 401 invalid_token (held)
- sign-out answered 204

### 1. `POST`

Request:

```http
POST /api/auth/v1/sessions
Accept: application/json
X-Correlation-ID: 3464af8f-4b5d-4909-9e65-96a41163baa3
User-Agent: mosaiq-acceptance/1
Content-Type: application/json

{
  "email": "f20-01-store-manager@example.test",
  "password": "<masked #1>",
  "device_label": "F20-01 refusal check"
}
```

Response:

```http
201
Date: Tue, 24 Nov 2026 03:52:47 GMT
Content-Type: application/json
Content-Length: 881
Connection: close
Server: cloudflare
x-correlation-id: 3464af8f-4b5d-4909-9e65-96a41163baa3
strict-transport-security: max-age=31536000; includeSubDomains
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: strict-origin-when-cross-origin
content-security-policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
permissions-policy: camera=(), microphone=(), geolocation=(), payment=()
cf-cache-status: DYNAMIC
Report-To: {"group":"cf-nel","max_age":604800,"endpoints":[{"url":"https://a.nel.cloudflare.com/report/v4?s=1%2B4lNLQ0%2BSfPN8SAgYvOS3g6J778X0XRSZKxKVtSxZ6BoFZBNHf8NLJ6gEHp6maVme2BHr5c3ECDuiZWjdfHHPbCabxUTWivYspO7LO8tAOMfVHknKe7Qf3KdYJxIA4kn2rAh1sHc05hMRJY"}]}
Nel: {"report_to":"cf-nel","success_fraction":0.0,"max_age":604800}
CF-RAY: a5f5767b5ef90a16-DFW
alt-svc: h3=":443"; ma=86400

{
  "access_token": "<masked #2>",
  "refresh_token": "<masked #3>",
  "token_type": "Bearer",
  "expires_in": 600,
  "expires_at": "2026-11-24T04:02:47Z"
}
```

### 2. `GET`

Request:

```http
GET /api/users/v1/users
Accept: application/json
X-Correlation-ID: c2faa54b-be6e-4da8-aad8-8bac8fdf0c91
User-Agent: mosaiq-acceptance/1
Authorization: Bearer <masked #2>
```

Response:

```http
403
Date: Tue, 24 Nov 2026 03:52:47 GMT
Content-Type: application/json
Content-Length: 160
Connection: close
Server: cloudflare
x-correlation-id: c2faa54b-be6e-4da8-aad8-8bac8fdf0c91
strict-transport-security: max-age=31536000; includeSubDomains
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: strict-origin-when-cross-origin
content-security-policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
permissions-policy: camera=(), microphone=(), geolocation=(), payment=()
cf-cache-status: DYNAMIC
Report-To: {"group":"cf-nel","max_age":604800,"endpoints":[{"url":"https://a.nel.cloudflare.com/report/v4?s=n0ZflPC37qmYP3rsX9lUBBnM8DLfbFghgVP0HfK8LmGjV1bvAAYU%2B9wMI4Zq3gr2vwpEPCX7eIyJlHkDEfUOnMPA%2BJI8T2h4jGifm0yfn6E5j5ANxZDEdPI4U%2FymvGJeQCm%2FJCbQ1FTE36cQ"}]}
Nel: {"report_to":"cf-nel","success_fraction":0.0,"max_age":604800}
CF-RAY: a5f576804a31cdd0-DFW
alt-svc: h3=":443"; ma=86400

{
  "code": "forbidden_role",
  "message": "The authenticated role cannot perform this operation.",
  "correlation_id": "c2faa54b-be6e-4da8-aad8-8bac8fdf0c91",
  "details": []
}
```

### 3. `POST`

Request:

```http
POST /api/sales/v1/sales
Accept: application/json
X-Correlation-ID: eb7491b0-a860-4646-8949-5a4adb8c0c47
User-Agent: mosaiq-acceptance/1
Content-Type: application/json

{}
```

Response:

```http
401
Date: Tue, 24 Nov 2026 03:52:47 GMT
Content-Type: application/json
Content-Length: 134
Connection: close
Server: cloudflare
x-correlation-id: eb7491b0-a860-4646-8949-5a4adb8c0c47
strict-transport-security: max-age=31536000; includeSubDomains
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: strict-origin-when-cross-origin
content-security-policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
permissions-policy: camera=(), microphone=(), geolocation=(), payment=()
cf-cache-status: DYNAMIC
Report-To: {"group":"cf-nel","max_age":604800,"endpoints":[{"url":"https://a.nel.cloudflare.com/report/v4?s=NyyCoGAW3EAwrjj%2BbTIDdmS9gTOIil3SfAbOMHvfXNA1ELHlLot1ROX%2FTyl%2BlhajayIzwCZrFIFyiKdaMdVlXrz7qSbSGU5uweIB1bsiEK%2BSYK51ZhiFsHzdfm0DYl6YUB%2FTRRWOo0QyUMiF"}]}
Nel: {"report_to":"cf-nel","success_fraction":0.0,"max_age":604800}
CF-RAY: a5f576817b481867-DFW
alt-svc: h3=":443"; ma=86400

{
  "code": "invalid_token",
  "message": "The bearer token is invalid.",
  "correlation_id": "eb7491b0-a860-4646-8949-5a4adb8c0c47",
  "details": []
}
```

### 4. `DELETE`

Request:

```http
DELETE /api/auth/v1/sessions/current
Accept: application/json
X-Correlation-ID: 880a2acd-8c9f-4c92-a9d8-efdd520edb61
User-Agent: mosaiq-acceptance/1
Authorization: Bearer <masked #2>
```

Response:

```http
204
Date: Tue, 24 Nov 2026 03:52:48 GMT
Connection: close
Server: cloudflare
x-correlation-id: 880a2acd-8c9f-4c92-a9d8-efdd520edb61
strict-transport-security: max-age=31536000; includeSubDomains
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: strict-origin-when-cross-origin
content-security-policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
permissions-policy: camera=(), microphone=(), geolocation=(), payment=()
cf-cache-status: DYNAMIC
Report-To: {"group":"cf-nel","max_age":604800,"endpoints":[{"url":"https://a.nel.cloudflare.com/report/v4?s=enVTuGJwDX6beyW8UBm9ecaeCH4N49%2FsTOy5zzITRBXA3IZFVxONxiudhufzuxSnWvYkGByuMQWFJvnZSfYxWBrClIyf5%2F4zOC%2FbxOaDhW2TdV53BT0Ez89XgY21cYreLH33ALKCT7gDvbzu"}]}
Nel: {"report_to":"cf-nel","success_fraction":0.0,"max_age":604800}
CF-RAY: a5f576823f0e6c3a-DFW
alt-svc: h3=":443"; ma=86400
```

