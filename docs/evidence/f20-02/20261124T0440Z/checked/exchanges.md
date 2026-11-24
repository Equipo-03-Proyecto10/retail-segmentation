# F20-02 Electron reads the sale and writes a draft: requests and responses

<a id="exchange-1"></a>
## 1. POST /api/auth/v1/sessions (desktop client, staff account)

`X-Correlation-ID: dc37ce35-7c86-4ff7-b2a5-0d0888831603`, at 2026-11-24T04:39:03Z

```http
POST https://mosaiq.maxthecoder.online/api/auth/v1/sessions
Accept: application/xml
X-Correlation-ID: dc37ce35-7c86-4ff7-b2a5-0d0888831603
Content-Type: application/xml

<?xml version="1.0" encoding="UTF-8"?><SignInRequest xmlns="https://mosaiq.maxthecoder.online/xml/auth/v1"><email>f20-01-store-manager@example.test</email><password><masked #1></password><device_label>MOSAIQ Desktop F20-02 evidence</device_label></SignInRequest>
```

```http
HTTP 201
content-type: application/xml
x-correlation-id: dc37ce35-7c86-4ff7-b2a5-0d0888831603

<?xml version='1.0' encoding='UTF-8'?>
<TokenResult xmlns="https://mosaiq.maxthecoder.online/xml/auth/v1" xmlns:common="https://mosaiq.maxthecoder.online/xml/common/v1"><access_token><masked #2></access_token><refresh_token><masked #3></refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2026-11-24T04:48:48Z</expires_at></TokenResult>
```

<a id="exchange-2"></a>
## 2. GET /api/auth/v1/sessions/current (desktop client, staff account)

`X-Correlation-ID: e0c6f8d0-2f58-47b2-9bba-666370515ec7`, at 2026-11-24T04:39:03Z

```http
GET https://mosaiq.maxthecoder.online/api/auth/v1/sessions/current
Accept: application/xml
X-Correlation-ID: e0c6f8d0-2f58-47b2-9bba-666370515ec7
Authorization: Bearer <masked #2>


```

```http
HTTP 200
content-type: application/xml
x-correlation-id: e0c6f8d0-2f58-47b2-9bba-666370515ec7

<?xml version='1.0' encoding='UTF-8'?>
<SessionClaims xmlns="https://mosaiq.maxthecoder.online/xml/auth/v1" xmlns:common="https://mosaiq.maxthecoder.online/xml/common/v1"><sub>927d1bf9-c8cb-43ca-9841-704e5d0993bb</sub><sid>47256b04-4aea-4222-a3c2-c4e8a1be7407</sid><role>STORE_MANAGER</role><issued_at>2026-11-24T04:38:48Z</issued_at><expires_at>2026-11-24T04:48:48Z</expires_at></SessionClaims>
```

<a id="exchange-3"></a>
## 3. GET /api/sales/v1/sales/307 (desktop client, staff account)

`X-Correlation-ID: f313cff2-3207-4f8b-95be-361f4abf560a`, at 2026-11-24T04:39:03Z

```http
GET https://mosaiq.maxthecoder.online/api/sales/v1/sales/307
Accept: application/xml
X-Correlation-ID: f313cff2-3207-4f8b-95be-361f4abf560a
Authorization: Bearer <masked #2>


```

```http
HTTP 200
content-type: application/xml
x-correlation-id: f313cff2-3207-4f8b-95be-361f4abf560a

<?xml version='1.0' encoding='UTF-8'?>
<Sale xmlns="https://mosaiq.maxthecoder.online/xml/sales/v1" xmlns:common="https://mosaiq.maxthecoder.online/xml/common/v1"><sale_id>307</sale_id><customer_id>00000000-0000-0000-0000-000000000028</customer_id><store_id>30</store_id><channel_id>3</channel_id><occurred_at>2026-11-24T03:51:10.148779Z</occurred_at><total>41.25</total><lines><product_id>1</product_id><quantity>1</quantity><unit_price>18.75</unit_price></lines><lines><product_id>2</product_id><quantity>1</quantity><unit_price>22.50</unit_price></lines></Sale>
```

<a id="exchange-4"></a>
## 4. GET /api/products/v1/products (desktop client, staff account)

`X-Correlation-ID: 236297f8-feb9-4ff1-bc7e-a2f156e15a9b`, at 2026-11-24T04:39:03Z

```http
GET https://mosaiq.maxthecoder.online/api/products/v1/products?ids=1%2C2&size=50
Accept: application/xml
X-Correlation-ID: 236297f8-feb9-4ff1-bc7e-a2f156e15a9b
Authorization: Bearer <masked #2>


```

```http
HTTP 200
content-type: application/xml
x-correlation-id: 236297f8-feb9-4ff1-bc7e-a2f156e15a9b

<?xml version='1.0' encoding='UTF-8'?>
<Products xmlns="https://mosaiq.maxthecoder.online/xml/products/v1" xmlns:common="https://mosaiq.maxthecoder.online/xml/common/v1"><Product><product_id>1</product_id><sku>SKU-00001</sku><name>Demo Product 1</name><category_id>1</category_id><list_price>18.75</list_price><is_active>true</is_active></Product><Product><product_id>2</product_id><sku>SKU-00002</sku><name>Demo Product 2</name><category_id>2</category_id><list_price>22.50</list_price><is_active>true</is_active></Product><common:PageInfo><common:page>1</common:page><common:size>50</common:size><common:total>2</common:total></common:PageInfo></Products>
```

<a id="exchange-5"></a>
## 5. GET /api/stores/v1/stores (desktop client, staff account)

`X-Correlation-ID: 08b5fde1-e967-40ca-ba93-2c7af9368338`, at 2026-11-24T04:39:03Z

```http
GET https://mosaiq.maxthecoder.online/api/stores/v1/stores?page=1&size=100
Accept: application/xml
X-Correlation-ID: 08b5fde1-e967-40ca-ba93-2c7af9368338
Authorization: Bearer <masked #2>


```

```http
HTTP 200
content-type: application/xml
x-correlation-id: 08b5fde1-e967-40ca-ba93-2c7af9368338

<?xml version='1.0' encoding='UTF-8'?>
<Stores xmlns="https://mosaiq.maxthecoder.online/xml/stores/v1" xmlns:common="https://mosaiq.maxthecoder.online/xml/common/v1"><Store><store_id>1</store_id><name>Store 1</name><city>Monterrey</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.6866</latitude><longitude>-100.3161</longitude><geofence_radius_m>110</geofence_radius_m><set_at>2026-11-23T19:16:25.843243Z</set_at></location></Store><Store><store_id>2</store_id><name>Store 2</name><city>Guadalupe</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.6776</latitude><longitude>-100.2597</longitude><geofence_radius_m>120</geofence_radius_m><set_at>2026-11-23T18:16:25.843243Z</set_at></location></Store><Store><store_id>3</store_id><name>Store 3</name><city>San Nicolás</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.7417</latitude><longitude>-100.3022</longitude><geofence_radius_m>130</geofence_radius_m><set_at>2026-11-23T17:16:25.843243Z</set_at></location></Store><Store><store_id>4</store_id><name>Store 4</name><city>Apodaca</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.7816</latitude><longitude>-100.1887</longitude><geofence_radius_m>140</geofence_radius_m><set_at>2026-11-23T16:16:25.843243Z</set_at></location></Store><Store><store_id>5</store_id><name>Store 5</name><city>Guadalajara</city><state>Jalisco</state><is_active>true</is_active><location><latitude>20.6597</latitude><longitude>-103.3496</longitude><geofence_radius_m>150</geofence_radius_m><set_at>2026-11-23T15:16:25.843243Z</set_at></location></Store><Store><store_id>6</store_id><name>Store 6</name><city>CDMX</city><state>CDMX</state><is_active>true</is_active><location><latitude>19.4326</latitude><longitude>-99.1332</longitude><geofence_radius_m>160</geofence_radius_m><set_at>2026-11-23T14:16:25.843243Z</set_at></location></Store><Store><store_id>7</store_id><name>Store 7</name><city>Puebla</city><state>Puebla</state><is_active>true</is_active><location><latitude>19.0414</latitude><longitude>-98.2063</longitude><geofence_radius_m>170</geofence_radius_m><set_at>2026-11-23T13:16:25.843243Z</set_at></location></Store><Store><store_id>8</store_id><name>Store 8</name><city>Querétaro</city><state>Querétaro</state><is_active>true</is_active><location><latitude>20.5888</latitude><longitude>-100.3899</longitude><geofence_radius_m>180</geofence_radius_m><set_at>2026-11-23T12:16:25.843243Z</set_at></location></Store><Store><store_id>9</store_id><name>Store 9</name><city>Tijuana</city><state>Baja California</state><is_active>true</is_active><location><latitude>32.5149</latitude><longitude>-117.0382</longitude><geofence_radius_m>190</geofence_radius_m><set_at>2026-11-23T11:16:25.843243Z</set_at></location></Store><Store><store_id>10</store_id><name>Store 10</name><city>Mérida</city><state>Yucatán</state><is_active>true</is_active><location><latitude>20.9674</latitude><longitude>-89.5926</longitude><geofence_radius_m>200</geofence_radius_m><set_at>2026-11-23T10:16:25.843243Z</set_at></location></Store><Store><store_id>11</store_id><name>Store 11</name><city>Monterrey</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.6866</latitude><longitude>-100.3161</longitude><geofence_radius_m>210</geofence_radius_m><set_at>2026-11-23T09:16:25.843243Z</set_at></location></Store><Store><store_id>12</store_id><name>Store 12</name><city>Guadalupe</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.6776</latitude><longitude>-100.2597</longitude><geofence_radius_m>220</geofence_radius_m><set_at>2026-11-23T08:16:25.843243Z</set_at></location></Store><Store><store_id>13</store_id><name>Store 13</name><city>San Nicolás</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.7417</latitude><longitude>-100.3022</longitude><geofence_radius_m>230</geofence_radius_m><set_at>2026-11-23T07:16:25.843243Z</set_at></location></Store><Store><store_id>14</store_id><name>Store 14</name><city>Apodaca</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.7816</latitude><longitude>-100.1887</longitude><geofence_radius_m>240</geofence_radius_m><set_at>2026-11-23T06:16:25.843243Z</set_at></location></Store><Store><store_id>15</store_id><name>Store 15</name><city>Guadalajara</city><state>Jalisco</state><is_active>true</is_active><location><latitude>20.6597</latitude><longitude>-103.3496</longitude><geofence_radius_m>250</geofence_radius_m><set_at>2026-11-23T05:16:25.843243Z</set_at></location></Store><Store><store_id>16</store_id><name>Store 16</name><city>CDMX</city><state>CDMX</state><is_active>true</is_active><location><latitude>19.4326</latitude><longitude>-99.1332</longitude><geofence_radius_m>260</geofence_radius_m><set_at>2026-11-23T04:16:25.843243Z</set_at></location></Store><Store><store_id>17</store_id><name>Store 17</name><city>Puebla</city><state>Puebla</state><is_active>true</is_active><location><latitude>19.0414</latitude><longitude>-98.2063</longitude><geofence_radius_m>270</geofence_radius_m><set_at>2026-11-23T03:16:25.843243Z</set_at></location></Store><Store><store_id>18</store_id><name>Store 18</name><city>Querétaro</city><state>Querétaro</state><is_active>true</is_active><location><latitude>20.5888</latitude><longitude>-100.3899</longitude><geofence_radius_m>280</geofence_radius_m><set_at>2026-11-23T02:16:25.843243Z</set_at></location></Store><Store><store_id>19</store_id><name>Store 19</name><city>Tijuana</city><state>Baja California</state><is_active>true</is_active><location><latitude>32.5149</latitude><longitude>-117.0382</longitude><geofence_radius_m>290</geofence_radius_m><set_at>2026-11-23T01:16:25.843243Z</set_at></location></Store><Store><store_id>20</store_id><name>Store 20</name><city>Mérida</city><state>Yucatán</state><is_active>true</is_active><location><latitude>20.9674</latitude><longitude>-89.5926</longitude><geofence
… (3203 more characters)
```

<a id="exchange-6"></a>
## 6. GET /api/sales/v1/channels (desktop client, staff account)

`X-Correlation-ID: 412b7428-9eff-4877-b8b4-795c7c8d0b61`, at 2026-11-24T04:39:03Z

```http
GET https://mosaiq.maxthecoder.online/api/sales/v1/channels
Accept: application/xml
X-Correlation-ID: 412b7428-9eff-4877-b8b4-795c7c8d0b61
Authorization: Bearer <masked #2>


```

```http
HTTP 200
content-type: application/xml
x-correlation-id: 412b7428-9eff-4877-b8b4-795c7c8d0b61

<?xml version='1.0' encoding='UTF-8'?>
<Channels xmlns="https://mosaiq.maxthecoder.online/xml/sales/v1" xmlns:common="https://mosaiq.maxthecoder.online/xml/common/v1"><Channel><channel_id>1</channel_id><name>mobile_app</name></Channel><Channel><channel_id>2</channel_id><name>web</name></Channel><Channel><channel_id>3</channel_id><name>physical_store</name></Channel><Channel><channel_id>4</channel_id><name>marketplace</name></Channel><Channel><channel_id>5</channel_id><name>call_center</name></Channel></Channels>
```

<a id="exchange-7"></a>
## 7. DELETE /api/auth/v1/sessions/current (desktop client, staff account)

`X-Correlation-ID: b336537c-bcec-4d77-ab26-167f661227ac`, at 2026-11-24T04:39:03Z

```http
DELETE https://mosaiq.maxthecoder.online/api/auth/v1/sessions/current
Accept: application/xml
X-Correlation-ID: b336537c-bcec-4d77-ab26-167f661227ac
Authorization: Bearer <masked #2>


```

```http
HTTP 204
x-correlation-id: b336537c-bcec-4d77-ab26-167f661227ac


```

<a id="exchange-8"></a>
## 8. POST /api/auth/v1/sessions (desktop client, marketing account)

`X-Correlation-ID: 08228b5b-d1ff-4902-95e7-fe6c03a7785f`, at 2026-11-24T04:39:03Z

```http
POST https://mosaiq.maxthecoder.online/api/auth/v1/sessions
Accept: application/xml
X-Correlation-ID: 08228b5b-d1ff-4902-95e7-fe6c03a7785f
Content-Type: application/xml

<?xml version="1.0" encoding="UTF-8"?><SignInRequest xmlns="https://mosaiq.maxthecoder.online/xml/auth/v1"><email>f20-02-marketing@example.test</email><password><masked #4></password><device_label>MOSAIQ Desktop F20-02 evidence</device_label></SignInRequest>
```

```http
HTTP 201
content-type: application/xml
x-correlation-id: 08228b5b-d1ff-4902-95e7-fe6c03a7785f

<?xml version='1.0' encoding='UTF-8'?>
<TokenResult xmlns="https://mosaiq.maxthecoder.online/xml/auth/v1" xmlns:common="https://mosaiq.maxthecoder.online/xml/common/v1"><access_token><masked #5></access_token><refresh_token><masked #6></refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2026-11-24T04:48:50Z</expires_at></TokenResult>
```

<a id="exchange-9"></a>
## 9. GET /api/auth/v1/sessions/current (desktop client, marketing account)

`X-Correlation-ID: 46f6986b-5f29-42e2-b06d-59062238b9a0`, at 2026-11-24T04:39:03Z

```http
GET https://mosaiq.maxthecoder.online/api/auth/v1/sessions/current
Accept: application/xml
X-Correlation-ID: 46f6986b-5f29-42e2-b06d-59062238b9a0
Authorization: Bearer <masked #5>


```

```http
HTTP 200
content-type: application/xml
x-correlation-id: 46f6986b-5f29-42e2-b06d-59062238b9a0

<?xml version='1.0' encoding='UTF-8'?>
<SessionClaims xmlns="https://mosaiq.maxthecoder.online/xml/auth/v1" xmlns:common="https://mosaiq.maxthecoder.online/xml/common/v1"><sub>661fec2f-94bf-4bd4-8a50-86daa1fd0804</sub><sid>bc315898-edab-448a-9b52-826527b0b438</sid><role>MARKETING</role><issued_at>2026-11-24T04:38:50Z</issued_at><expires_at>2026-11-24T04:48:50Z</expires_at></SessionClaims>
```

<a id="exchange-10"></a>
## 10. POST /api/campaigns/v1/campaigns (desktop client, marketing account)

`X-Correlation-ID: cf3be364-ee21-4ac8-9fa3-4f6807d9cd44`, at 2026-11-24T04:39:03Z

```http
POST https://mosaiq.maxthecoder.online/api/campaigns/v1/campaigns
Accept: application/xml
X-Correlation-ID: cf3be364-ee21-4ac8-9fa3-4f6807d9cd44
Content-Type: application/xml
Authorization: Bearer <masked #5>

<?xml version="1.0" encoding="UTF-8"?><CreateCampaignRequest xmlns="https://mosaiq.maxthecoder.online/xml/campaigns/v1"><name>F20-02 evidence draft 20261124T0438Z</name><label_code>CHAMPION</label_code><starts_on>2026-12-01</starts_on><ends_on>2026-12-31</ends_on></CreateCampaignRequest>
```

```http
HTTP 201
content-type: application/xml
location: /v1/campaigns/31
x-correlation-id: cf3be364-ee21-4ac8-9fa3-4f6807d9cd44

<?xml version='1.0' encoding='UTF-8'?>
<Campaign xmlns="https://mosaiq.maxthecoder.online/xml/campaigns/v1" xmlns:common="https://mosaiq.maxthecoder.online/xml/common/v1"><campaign_id>31</campaign_id><name>F20-02 evidence draft 20261124T0438Z</name><label_code>CHAMPION</label_code><starts_on>2026-12-01</starts_on><ends_on>2026-12-31</ends_on><status>DRAFT</status><allowed_transitions>ACTIVE</allowed_transitions><allowed_transitions>CANCELLED</allowed_transitions></Campaign>
```

<a id="exchange-11"></a>
## 11. DELETE /api/auth/v1/sessions/current (desktop client, marketing account)

`X-Correlation-ID: e0b940ef-18a4-4e53-a600-4cdf664bd42c`, at 2026-11-24T04:39:03Z

```http
DELETE https://mosaiq.maxthecoder.online/api/auth/v1/sessions/current
Accept: application/xml
X-Correlation-ID: e0b940ef-18a4-4e53-a600-4cdf664bd42c
Authorization: Bearer <masked #5>


```

```http
HTTP 204
x-correlation-id: e0b940ef-18a4-4e53-a600-4cdf664bd42c


```

<a id="exchange-12"></a>
## 12. POST /api/auth/v1/sessions (desktop client, forbidden account)

`X-Correlation-ID: 78484361-4d92-4c31-8207-222ca3de75c4`, at 2026-11-24T04:39:03Z

```http
POST https://mosaiq.maxthecoder.online/api/auth/v1/sessions
Accept: application/xml
X-Correlation-ID: 78484361-4d92-4c31-8207-222ca3de75c4
Content-Type: application/xml

<?xml version="1.0" encoding="UTF-8"?><SignInRequest xmlns="https://mosaiq.maxthecoder.online/xml/auth/v1"><email>f20-02-analyst@example.test</email><password><masked #7></password><device_label>MOSAIQ Desktop F20-02 evidence</device_label></SignInRequest>
```

```http
HTTP 201
content-type: application/xml
x-correlation-id: 78484361-4d92-4c31-8207-222ca3de75c4

<?xml version='1.0' encoding='UTF-8'?>
<TokenResult xmlns="https://mosaiq.maxthecoder.online/xml/auth/v1" xmlns:common="https://mosaiq.maxthecoder.online/xml/common/v1"><access_token><masked #8></access_token><refresh_token><masked #9></refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2026-11-24T04:48:52Z</expires_at></TokenResult>
```

<a id="exchange-13"></a>
## 13. GET /api/auth/v1/sessions/current (desktop client, forbidden account)

`X-Correlation-ID: 79c77184-0504-4d4b-957d-eed2f22df4b0`, at 2026-11-24T04:39:03Z

```http
GET https://mosaiq.maxthecoder.online/api/auth/v1/sessions/current
Accept: application/xml
X-Correlation-ID: 79c77184-0504-4d4b-957d-eed2f22df4b0
Authorization: Bearer <masked #8>


```

```http
HTTP 200
content-type: application/xml
x-correlation-id: 79c77184-0504-4d4b-957d-eed2f22df4b0

<?xml version='1.0' encoding='UTF-8'?>
<SessionClaims xmlns="https://mosaiq.maxthecoder.online/xml/auth/v1" xmlns:common="https://mosaiq.maxthecoder.online/xml/common/v1"><sub>517354fc-758d-4515-a514-6ab94154483d</sub><sid>c8edcf5a-1019-492f-8c8c-270efe641e36</sid><role>ANALYST</role><issued_at>2026-11-24T04:38:52Z</issued_at><expires_at>2026-11-24T04:48:52Z</expires_at></SessionClaims>
```

<a id="exchange-14"></a>
## 14. POST /api/campaigns/v1/campaigns (desktop client, forbidden account)

`X-Correlation-ID: b4f71f52-e013-412e-a295-5dc9b398e8b6`, at 2026-11-24T04:39:03Z

```http
POST https://mosaiq.maxthecoder.online/api/campaigns/v1/campaigns
Accept: application/xml
X-Correlation-ID: b4f71f52-e013-412e-a295-5dc9b398e8b6
Content-Type: application/xml
Authorization: Bearer <masked #8>

<?xml version="1.0" encoding="UTF-8"?><CreateCampaignRequest xmlns="https://mosaiq.maxthecoder.online/xml/campaigns/v1"><name>F20-02 evidence draft 20261124T0438Z</name><label_code>CHAMPION</label_code><starts_on>2026-12-01</starts_on><ends_on>2026-12-31</ends_on></CreateCampaignRequest>
```

```http
HTTP 403
content-type: application/xml
x-correlation-id: b4f71f52-e013-412e-a295-5dc9b398e8b6

<?xml version='1.0' encoding='UTF-8'?>
<Error xmlns="https://mosaiq.maxthecoder.online/xml/common/v1" xmlns:common="https://mosaiq.maxthecoder.online/xml/common/v1"><code>forbidden_role</code><message>The authenticated role cannot perform this operation.</message><correlation_id>b4f71f52-e013-412e-a295-5dc9b398e8b6</correlation_id></Error>
```

<a id="exchange-15"></a>
## 15. DELETE /api/auth/v1/sessions/current (desktop client, forbidden account)

`X-Correlation-ID: f484c3c5-765e-4dae-a276-b0f1e300c455`, at 2026-11-24T04:39:03Z

```http
DELETE https://mosaiq.maxthecoder.online/api/auth/v1/sessions/current
Accept: application/xml
X-Correlation-ID: f484c3c5-765e-4dae-a276-b0f1e300c455
Authorization: Bearer <masked #8>


```

```http
HTTP 204
x-correlation-id: f484c3c5-765e-4dae-a276-b0f1e300c455


```

<a id="exchange-16"></a>
## 16. GET /api/auth/openapi.yaml (version)

`X-Correlation-ID: f20-02-d3a18a4d29e84f7c`, at 2026-11-24T04:39:49Z

```http
GET https://mosaiq.maxthecoder.online/api/auth/openapi.yaml
X-Correlation-ID: f20-02-d3a18a4d29e84f7c


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f20-02-d3a18a4d29e84f7c

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

<a id="exchange-17"></a>
## 17. GET /api/sales/openapi.yaml (version)

`X-Correlation-ID: f20-02-58a4955ac01e47ad`, at 2026-11-24T04:39:49Z

```http
GET https://mosaiq.maxthecoder.online/api/sales/openapi.yaml
X-Correlation-ID: f20-02-58a4955ac01e47ad


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f20-02-58a4955ac01e47ad

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

<a id="exchange-18"></a>
## 18. GET /api/campaigns/openapi.yaml (version)

`X-Correlation-ID: f20-02-e01fbddfe3b44a25`, at 2026-11-24T04:39:49Z

```http
GET https://mosaiq.maxthecoder.online/api/campaigns/openapi.yaml
X-Correlation-ID: f20-02-e01fbddfe3b44a25


```

```http
HTTP 200
Content-Type: application/yaml
x-correlation-id: f20-02-e01fbddfe3b44a25

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
