# F20-02: the desktop client's requests and responses

Base URL `https://mosaiq.maxthecoder.online`, 2026-11-24T04:38:49Z – 2026-11-24T04:38:55Z.

<a id="exchange-1"></a>
## 1. POST /api/auth/v1/sessions (staff)

`X-Correlation-ID: dc37ce35-7c86-4ff7-b2a5-0d0888831603`, at 2026-11-24T04:38:49Z

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
## 2. GET /api/auth/v1/sessions/current (staff)

`X-Correlation-ID: e0c6f8d0-2f58-47b2-9bba-666370515ec7`, at 2026-11-24T04:38:49Z

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
## 3. GET /api/sales/v1/sales/307 (staff)

`X-Correlation-ID: f313cff2-3207-4f8b-95be-361f4abf560a`, at 2026-11-24T04:38:50Z

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
## 4. GET /api/products/v1/products (staff)

`X-Correlation-ID: 236297f8-feb9-4ff1-bc7e-a2f156e15a9b`, at 2026-11-24T04:38:50Z

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
## 5. GET /api/stores/v1/stores (staff)

`X-Correlation-ID: 08b5fde1-e967-40ca-ba93-2c7af9368338`, at 2026-11-24T04:38:50Z

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
<Stores xmlns="https://mosaiq.maxthecoder.online/xml/stores/v1" xmlns:common="https://mosaiq.maxthecoder.online/xml/common/v1"><Store><store_id>1</store_id><name>Store 1</name><city>Monterrey</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.6866</latitude><longitude>-100.3161</longitude><geofence_radius_m>110</geofence_radius_m><set_at>2026-11-23T19:16:25.843243Z</set_at></location></Store><Store><store_id>2</store_id><name>Store 2</name><city>Guadalupe</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.6776</latitude><longitude>-100.2597</longitude><geofence_radius_m>120</geofence_radius_m><set_at>2026-11-23T18:16:25.843243Z</set_at></location></Store><Store><store_id>3</store_id><name>Store 3</name><city>San Nicolás</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.7417</latitude><longitude>-100.3022</longitude><geofence_radius_m>130</geofence_radius_m><set_at>2026-11-23T17:16:25.843243Z</set_at></location></Store><Store><store_id>4</store_id><name>Store 4</name><city>Apodaca</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.7816</latitude><longitude>-100.1887</longitude><geofence_radius_m>140</geofence_radius_m><set_at>2026-11-23T16:16:25.843243Z</set_at></location></Store><Store><store_id>5</store_id><name>Store 5</name><city>Guadalajara</city><state>Jalisco</state><is_active>true</is_active><location><latitude>20.6597</latitude><longitude>-103.3496</longitude><geofence_radius_m>150</geofence_radius_m><set_at>2026-11-23T15:16:25.843243Z</set_at></location></Store><Store><store_id>6</store_id><name>Store 6</name><city>CDMX</city><state>CDMX</state><is_active>true</is_active><location><latitude>19.4326</latitude><longitude>-99.1332</longitude><geofence_radius_m>160</geofence_radius_m><set_at>2026-11-23T14:16:25.843243Z</set_at></location></Store><Store><store_id>7</store_id><name>Store 7</name><city>Puebla</city><state>Puebla</state><is_active>true</is_active><location><latitude>19.0414</latitude><longitude>-98.2063</longitude><geofence_radius_m>170</geofence_radius_m><set_at>2026-11-23T13:16:25.843243Z</set_at></location></Store><Store><store_id>8</store_id><name>Store 8</name><city>Querétaro</city><state>Querétaro</state><is_active>true</is_active><location><latitude>20.5888</latitude><longitude>-100.3899</longitude><geofence_radius_m>180</geofence_radius_m><set_at>2026-11-23T12:16:25.843243Z</set_at></location></Store><Store><store_id>9</store_id><name>Store 9</name><city>Tijuana</city><state>Baja California</state><is_active>true</is_active><location><latitude>32.5149</latitude><longitude>-117.0382</longitude><geofence_radius_m>190</geofence_radius_m><set_at>2026-11-23T11:16:25.843243Z</set_at></location></Store><Store><store_id>10</store_id><name>Store 10</name><city>Mérida</city><state>Yucatán</state><is_active>true</is_active><location><latitude>20.9674</latitude><longitude>-89.5926</longitude><geofence_radius_m>200</geofence_radius_m><set_at>2026-11-23T10:16:25.843243Z</set_at></location></Store><Store><store_id>11</store_id><name>Store 11</name><city>Monterrey</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.6866</latitude><longitude>-100.3161</longitude><geofence_radius_m>210</geofence_radius_m><set_at>2026-11-23T09:16:25.843243Z</set_at></location></Store><Store><store_id>12</store_id><name>Store 12</name><city>Guadalupe</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.6776</latitude><longitude>-100.2597</longitude><geofence_radius_m>220</geofence_radius_m><set_at>2026-11-23T08:16:25.843243Z</set_at></location></Store><Store><store_id>13</store_id><name>Store 13</name><city>San Nicolás</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.7417</latitude><longitude>-100.3022</longitude><geofence_radius_m>230</geofence_radius_m><set_at>2026-11-23T07:16:25.843243Z</set_at></location></Store><Store><store_id>14</store_id><name>Store 14</name><city>Apodaca</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.7816</latitude><longitude>-100.1887</longitude><geofence_radius_m>240</geofence_radius_m><set_at>2026-11-23T06:16:25.843243Z</set_at></location></Store><Store><store_id>15</store_id><name>Store 15</name><city>Guadalajara</city><state>Jalisco</state><is_active>true</is_active><location><latitude>20.6597</latitude><longitude>-103.3496</longitude><geofence_radius_m>250</geofence_radius_m><set_at>2026-11-23T05:16:25.843243Z</set_at></location></Store><Store><store_id>16</store_id><name>Store 16</name><city>CDMX</city><state>CDMX</state><is_active>true</is_active><location><latitude>19.4326</latitude><longitude>-99.1332</longitude><geofence_radius_m>260</geofence_radius_m><set_at>2026-11-23T04:16:25.843243Z</set_at></location></Store><Store><store_id>17</store_id><name>Store 17</name><city>Puebla</city><state>Puebla</state><is_active>true</is_active><location><latitude>19.0414</latitude><longitude>-98.2063</longitude><geofence_radius_m>270</geofence_radius_m><set_at>2026-11-23T03:16:25.843243Z</set_at></location></Store><Store><store_id>18</store_id><name>Store 18</name><city>Querétaro</city><state>Querétaro</state><is_active>true</is_active><location><latitude>20.5888</latitude><longitude>-100.3899</longitude><geofence_radius_m>280</geofence_radius_m><set_at>2026-11-23T02:16:25.843243Z</set_at></location></Store><Store><store_id>19</store_id><name>Store 19</name><city>Tijuana</city><state>Baja California</state><is_active>true</is_active><location><latitude>32.5149</latitude><longitude>-117.0382</longitude><geofence_radius_m>290</geofence_radius_m><set_at>2026-11-23T01:16:25.843243Z</set_at></location></Store><Store><store_id>20</store_id><name>Store 20</name><city>Mérida</city><state>Yucatán</state><is_active>true</is_active><location><latitude>20.9674</latitude><longitude>-89.5926</longitude><geofence_radius_m>300</geofence_radius_m><set_at>2026-11-23T00:16:25.843243Z</set_at></location></Store><Store><store_id>21</store_id><name>Store 21</name><city>Monterrey</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.6866</latitude><longitude>-100.3161</longitude><geofence_radius_m>310</geofence_radius_m><set_at>2026-11-22T23:16:25.843243Z</set_at></location></Store><Store><store_id>22</store_id><name>Store 22</name><city>Guadalupe</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.6776</latitude><longitude>-100.2597</longitude><geofence_radius_m>320</geofence_radius_m><set_at>2026-11-22T22:16:25.843243Z</set_at></location></Store><Store><store_id>23</store_id><name>Store 23</name><city>San Nicolás</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.7417</latitude><longitude>-100.3022</longitude><geofence_radius_m>330</geofence_radius_m><set_at>2026-11-22T21:16:25.843243Z</set_at></location></Store><Store><store_id>24</store_id><name>Store 24</name><city>Apodaca</city><state>Nuevo León</state><is_active>true</is_active><location><latitude>25.7816</latitude><longitude>-100.1887</longitude><geofence_radius_m>340</geofence_radius_m><set_at>2026-11-22T20:16:25.843243Z</set_at></location></Store><Store><store_id>25</store_id><name>Store 25</name><city>Guadalajara</city><state>Jalisco</state><is_active>true</is_active><location><latitude>20.6597</latitude><longitude>-103.3496</longitude><geofence_radius_m>350</geofence_radius_m><set_at>2026-11-22T19:16:25.843243Z</set_at></location></Store><Store><store_id>26</store_id><name>Store 26</name><city>CDMX</city><state>CDMX</state><is_active>true</is_active><location><latitude>19.4326</latitude><longitude>-99.1332</longitude><geofence_radius_m>360</geofence_radius_m><set_at>2026-11-22T18:16:25.843243Z</set_at></location></Store><Store><store_id>27</store_id><name>Store 27</name><city>Puebla</city><state>Puebla</state><is_active>true</is_active><location><latitude>19.0414</latitude><longitude>-98.2063</longitude><geofence_radius_m>370</geofence_radius_m><set_at>2026-11-22T17:16:25.843243Z</set_at></location></Store><Store><store_id>28</store_id><name>Store 28</name><city>Querétaro</city><state>Querétaro</state><is_active>true</is_active><location><latitude>20.5888</latitude><longitude>-100.3899</longitude><geofence_radius_m>380</geofence_radius_m><set_at>2026-11-22T16:16:25.843243Z</set_at></location></Store><Store><store_id>29</store_id><name>Store 29</name><city>Tijuana</city><state>Baja California</state><is_active>true</is_active><location><latitude>32.5149</latitude><longitude>-117.0382</longitude><geofence_radius_m>390</geofence_radius_m><set_at>2026-11-22T15:16:25.843243Z</set_at></location></Store><Store><store_id>30</store_id><name>Store 30</name><city>Mérida</city><state>Yucatán</state><is_active>true</is_active><location><latitude>20.9674</latitude><longitude>-89.5926</longitude><geofence_radius_m>400</geofence_radius_m><set_at>2026-11-22T14:16:25.843243Z</set_at></location></Store><common:PageInfo><common:page>1</common:page><common:size>100</common:size><common:total>30</common:total></common:PageInfo></Stores>
```

<a id="exchange-6"></a>
## 6. GET /api/sales/v1/channels (staff)

`X-Correlation-ID: 412b7428-9eff-4877-b8b4-795c7c8d0b61`, at 2026-11-24T04:38:50Z

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
## 7. DELETE /api/auth/v1/sessions/current (staff)

`X-Correlation-ID: b336537c-bcec-4d77-ab26-167f661227ac`, at 2026-11-24T04:38:51Z

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
## 8. POST /api/auth/v1/sessions (marketing)

`X-Correlation-ID: 08228b5b-d1ff-4902-95e7-fe6c03a7785f`, at 2026-11-24T04:38:52Z

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
## 9. GET /api/auth/v1/sessions/current (marketing)

`X-Correlation-ID: 46f6986b-5f29-42e2-b06d-59062238b9a0`, at 2026-11-24T04:38:52Z

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
## 10. POST /api/campaigns/v1/campaigns (marketing)

`X-Correlation-ID: cf3be364-ee21-4ac8-9fa3-4f6807d9cd44`, at 2026-11-24T04:38:52Z

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
## 11. DELETE /api/auth/v1/sessions/current (marketing)

`X-Correlation-ID: e0b940ef-18a4-4e53-a600-4cdf664bd42c`, at 2026-11-24T04:38:53Z

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
## 12. POST /api/auth/v1/sessions (forbidden)

`X-Correlation-ID: 78484361-4d92-4c31-8207-222ca3de75c4`, at 2026-11-24T04:38:54Z

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
## 13. GET /api/auth/v1/sessions/current (forbidden)

`X-Correlation-ID: 79c77184-0504-4d4b-957d-eed2f22df4b0`, at 2026-11-24T04:38:54Z

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
## 14. POST /api/campaigns/v1/campaigns (forbidden)

`X-Correlation-ID: b4f71f52-e013-412e-a295-5dc9b398e8b6`, at 2026-11-24T04:38:54Z

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
## 15. DELETE /api/auth/v1/sessions/current (forbidden)

`X-Correlation-ID: f484c3c5-765e-4dae-a276-b0f1e300c455`, at 2026-11-24T04:38:55Z

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
