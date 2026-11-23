# F20-02 Electron reads the sale and writes a draft

Started 2026-11-22T22:41:29Z, finished 2026-11-22T22:41:30Z.
19 checks, 19 held, 0 did not.

- **desktop version:** `0.1.0`
- **desktop checkout commit:** `5cbbd2fef5e45c0c7557c1a7fb05c303257b7cca`
- **desktop checkout describe:** `kit-py/v0.1.8-601-g5cbbd2fe`
- **desktop settings.json:** `<scratch>/cfg/settings.json`
- **desktop base URL:** `http://127.0.0.1:18760`
- **desktop run:** `2026-11-22T22:41:04Z – 2026-11-22T22:41:09Z`
- **staff account:** `user3@mosaiq-demo.com (STORE_MANAGER)`
- **xmllint, staff response:** `contracts/sales/sales.xsd: 003-staff-response.xml validates`
- **sale X-Correlation-ID:** `92cdcdc1-e61a-46d2-b746-9e06b7ea4883`
- **marketing account:** `user4@mosaiq-demo.com (MARKETING)`
- **xmllint, marketing request:** `contracts/campaigns/campaigns.xsd: 010-marketing-request.xml validates`
- **xmllint, marketing response:** `contracts/campaigns/campaigns.xsd: 010-marketing-response.xml validates`
- **campaign_id:** `32`
- **campaign X-Correlation-ID:** `ecb9a505-55b5-43f2-b68e-f02f59c29edd`
- **forbidden account:** `user2@mosaiq-demo.com (ANALYST)`
- **xmllint, forbidden response:** `contracts/common/v1/common.xsd: 014-forbidden-response.xml validates`
- **forbidden X-Correlation-ID:** `0cad30e7-0c5d-498e-9594-4c54caf240f2`

| Criterion | Case | Expected | Actual | Evidence | |
|---|---|---|---|---|---|
| 1. Electron reads the sale in XML | the staff account's role is in x-mosaiq-roles ['ADMIN', 'ANALYST', 'STORE_MANAGER', 'MARKETING', 'AUDITOR', 'SALES_ASSOCIATE', 'COMMERCIAL_SUPERVISOR'] | in | in |  | ✓ |
| 1. Electron reads the sale in XML | GET /api/sales/v1/sales/30 answers 200 application/xml | 200 application/xml | 200 application/xml | [1](exchanges.md#exchange-3) | ✓ |
| 1. Electron reads the sale in XML | the client accepted it (its own validation against the bundled sales.xsd) | True | True |  | ✓ |
| 1. Electron reads the sale in XML | xmllint: the response validates against contracts/sales/sales.xsd | contracts/sales/sales.xsd: valid | contracts/sales/sales.xsd: 003-staff-response.xml validates | [1](exchanges.md#exchange-3) | ✓ |
| 1. Electron reads the sale in XML | the XML's sale_id and total are the transaction row's | 30, 74.00 | 30, 74.00 | [1](rows.md#rows-1), [2](exchanges.md#exchange-3) | ✓ |
| 2. MARKETING creates a draft campaign in XML | the marketing account's role is in x-mosaiq-roles ['ADMIN', 'MARKETING'] | in | in |  | ✓ |
| 2. MARKETING creates a draft campaign in XML | POST /api/campaigns/v1/campaigns answers 201 | 201 | 201 | [1](exchanges.md#exchange-10) | ✓ |
| 2. MARKETING creates a draft campaign in XML | xmllint: the request validates against contracts/campaigns/campaigns.xsd | contracts/campaigns/campaigns.xsd: valid | contracts/campaigns/campaigns.xsd: 010-marketing-request.xml validates | [1](exchanges.md#exchange-10) | ✓ |
| 2. MARKETING creates a draft campaign in XML | xmllint: the response validates against contracts/campaigns/campaigns.xsd | contracts/campaigns/campaigns.xsd: valid | contracts/campaigns/campaigns.xsd: 010-marketing-response.xml validates | [1](exchanges.md#exchange-10) | ✓ |
| 2. MARKETING creates a draft campaign in XML | the response is the draft that was sent, in DRAFT | F20-02 evidence draft 20261122T2241Z \| CHAMPION \| 2026-11-29 \| 2026-12-29 \| DRAFT | F20-02 evidence draft 20261122T2241Z \| CHAMPION \| 2026-11-29 \| 2026-12-29 \| DRAFT | [1](exchanges.md#exchange-10) | ✓ |
| 2. MARKETING creates a draft campaign in XML | the campaign row is the response's | F20-02 evidence draft 20261122T2241Z \| CHAMPION \| 2026-11-29 \| 2026-12-29 \| DRAFT | F20-02 evidence draft 20261122T2241Z \| CHAMPION \| 2026-11-29 \| 2026-12-29 \| DRAFT | [1](rows.md#rows-2), [2](exchanges.md#exchange-10) | ✓ |
| 3. A role outside x-mosaiq-roles tries the same write | the forbidden account's role is outside x-mosaiq-roles ['ADMIN', 'MARKETING'] | outside | outside |  | ✓ |
| 3. A role outside x-mosaiq-roles tries the same write | the window draws no create control for this role (signed-in operations) | False | False |  | ✓ |
| 3. A role outside x-mosaiq-roles tries the same write | the same request body as the MARKETING step | identical | identical | [1](exchanges.md#exchange-14), [2](exchanges.md#exchange-10) | ✓ |
| 3. A role outside x-mosaiq-roles tries the same write | POST /api/campaigns/v1/campaigns answers 403 | 403 | 403 | [1](exchanges.md#exchange-14) | ✓ |
| 3. A role outside x-mosaiq-roles tries the same write | xmllint: the response validates against contracts/common/v1/common.xsd | contracts/common/v1/common.xsd: valid | contracts/common/v1/common.xsd: 014-forbidden-response.xml validates | [1](exchanges.md#exchange-14) | ✓ |
| 3. A role outside x-mosaiq-roles tries the same write | the envelope is common:Error forbidden_role, the request's correlation id | forbidden_role, 0cad30e7-0c5d-498e-9594-4c54caf240f2 | forbidden_role, 0cad30e7-0c5d-498e-9594-4c54caf240f2 | [1](exchanges.md#exchange-14) | ✓ |
| 3. A role outside x-mosaiq-roles tries the same write | the error the client hands the screen is that envelope | forbidden_role, 0cad30e7-0c5d-498e-9594-4c54caf240f2 | forbidden_role, 0cad30e7-0c5d-498e-9594-4c54caf240f2 |  | ✓ |
| 3. A role outside x-mosaiq-roles tries the same write | the refused write created nothing: one campaign has the draft's name | 1 | 1 | [1](rows.md#rows-3) | ✓ |

## Versions

| Piece | Version |
|---|---|
| checkout commit | `5cbbd2fef5e45c0c7557c1a7fb05c303257b7cca` |
| checkout describe | `kit-py/v0.1.8-601-g5cbbd2fe` |
| auth contract | `1.1.0` |
| sales contract | `1.9.0` |
| campaigns contract | `1.4.1` |
