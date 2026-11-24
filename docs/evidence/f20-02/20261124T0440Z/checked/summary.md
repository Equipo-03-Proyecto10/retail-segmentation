# F20-02 Electron reads the sale and writes a draft

Started 2026-11-24T04:39:03Z, finished 2026-11-24T04:39:49Z.
19 checks, 19 held, 0 did not.

- **desktop version:** `0.1.0`
- **desktop checkout commit:** `fdf0caedb301d5308ea804ff9cb9bb5b43ce73cb`
- **desktop checkout describe:** `kit-py/v0.1.8-776-gfdf0caed`
- **desktop settings.json:** `<scratch>/settings.json`
- **desktop base URL:** `https://mosaiq.maxthecoder.online`
- **desktop run:** `2026-11-24T04:38:49Z – 2026-11-24T04:38:55Z`
- **staff account:** `f20-01-store-manager@example.test (STORE_MANAGER)`
- **xmllint, staff response:** `contracts/sales/sales.xsd: 003-staff-response.xml validates`
- **sale X-Correlation-ID:** `f313cff2-3207-4f8b-95be-361f4abf560a`
- **marketing account:** `f20-02-marketing@example.test (MARKETING)`
- **xmllint, marketing request:** `contracts/campaigns/campaigns.xsd: 010-marketing-request.xml validates`
- **xmllint, marketing response:** `contracts/campaigns/campaigns.xsd: 010-marketing-response.xml validates`
- **campaign_id:** `31`
- **campaign X-Correlation-ID:** `cf3be364-ee21-4ac8-9fa3-4f6807d9cd44`
- **forbidden account:** `f20-02-analyst@example.test (ANALYST)`
- **xmllint, forbidden response:** `contracts/common/v1/common.xsd: 014-forbidden-response.xml validates`
- **forbidden X-Correlation-ID:** `b4f71f52-e013-412e-a295-5dc9b398e8b6`

| Criterion | Case | Expected | Actual | Evidence | |
|---|---|---|---|---|---|
| 1. Electron reads the sale in XML | the staff account's role is in x-mosaiq-roles ['ADMIN', 'ANALYST', 'STORE_MANAGER', 'MARKETING', 'AUDITOR', 'SALES_ASSOCIATE', 'COMMERCIAL_SUPERVISOR'] | in | in |  | ✓ |
| 1. Electron reads the sale in XML | GET /api/sales/v1/sales/307 answers 200 application/xml | 200 application/xml | 200 application/xml | [1](exchanges.md#exchange-3) | ✓ |
| 1. Electron reads the sale in XML | the client accepted it (its own validation against the bundled sales.xsd) | True | True |  | ✓ |
| 1. Electron reads the sale in XML | xmllint: the response validates against contracts/sales/sales.xsd | contracts/sales/sales.xsd: valid | contracts/sales/sales.xsd: 003-staff-response.xml validates | [1](exchanges.md#exchange-3) | ✓ |
| 1. Electron reads the sale in XML | the XML's sale_id and total are the transaction row's | 307, 41.25 | 307, 41.25 | [1](rows.md#rows-1), [2](exchanges.md#exchange-3) | ✓ |
| 2. MARKETING creates a draft campaign in XML | the marketing account's role is in x-mosaiq-roles ['ADMIN', 'MARKETING'] | in | in |  | ✓ |
| 2. MARKETING creates a draft campaign in XML | POST /api/campaigns/v1/campaigns answers 201 | 201 | 201 | [1](exchanges.md#exchange-10) | ✓ |
| 2. MARKETING creates a draft campaign in XML | xmllint: the request validates against contracts/campaigns/campaigns.xsd | contracts/campaigns/campaigns.xsd: valid | contracts/campaigns/campaigns.xsd: 010-marketing-request.xml validates | [1](exchanges.md#exchange-10) | ✓ |
| 2. MARKETING creates a draft campaign in XML | xmllint: the response validates against contracts/campaigns/campaigns.xsd | contracts/campaigns/campaigns.xsd: valid | contracts/campaigns/campaigns.xsd: 010-marketing-response.xml validates | [1](exchanges.md#exchange-10) | ✓ |
| 2. MARKETING creates a draft campaign in XML | the response is the draft that was sent, in DRAFT | F20-02 evidence draft 20261124T0438Z \| CHAMPION \| 2026-12-01 \| 2026-12-31 \| DRAFT | F20-02 evidence draft 20261124T0438Z \| CHAMPION \| 2026-12-01 \| 2026-12-31 \| DRAFT | [1](exchanges.md#exchange-10) | ✓ |
| 2. MARKETING creates a draft campaign in XML | the campaign row is the response's | F20-02 evidence draft 20261124T0438Z \| CHAMPION \| 2026-12-01 \| 2026-12-31 \| DRAFT | F20-02 evidence draft 20261124T0438Z \| CHAMPION \| 2026-12-01 \| 2026-12-31 \| DRAFT | [1](rows.md#rows-2), [2](exchanges.md#exchange-10) | ✓ |
| 3. A role outside x-mosaiq-roles tries the same write | the forbidden account's role is outside x-mosaiq-roles ['ADMIN', 'MARKETING'] | outside | outside |  | ✓ |
| 3. A role outside x-mosaiq-roles tries the same write | the window draws no create control for this role (signed-in operations) | False | False |  | ✓ |
| 3. A role outside x-mosaiq-roles tries the same write | the same request body as the MARKETING step | identical | identical | [1](exchanges.md#exchange-14), [2](exchanges.md#exchange-10) | ✓ |
| 3. A role outside x-mosaiq-roles tries the same write | POST /api/campaigns/v1/campaigns answers 403 | 403 | 403 | [1](exchanges.md#exchange-14) | ✓ |
| 3. A role outside x-mosaiq-roles tries the same write | xmllint: the response validates against contracts/common/v1/common.xsd | contracts/common/v1/common.xsd: valid | contracts/common/v1/common.xsd: 014-forbidden-response.xml validates | [1](exchanges.md#exchange-14) | ✓ |
| 3. A role outside x-mosaiq-roles tries the same write | the envelope is common:Error forbidden_role, the request's correlation id | forbidden_role, b4f71f52-e013-412e-a295-5dc9b398e8b6 | forbidden_role, b4f71f52-e013-412e-a295-5dc9b398e8b6 | [1](exchanges.md#exchange-14) | ✓ |
| 3. A role outside x-mosaiq-roles tries the same write | the error the client hands the screen is that envelope | forbidden_role, b4f71f52-e013-412e-a295-5dc9b398e8b6 | forbidden_role, b4f71f52-e013-412e-a295-5dc9b398e8b6 |  | ✓ |
| 3. A role outside x-mosaiq-roles tries the same write | the refused write created nothing: no campaign of the draft's name after campaign 31 | 0 | 0 | [1](rows.md#rows-3) | ✓ |

## Versions

| Piece | Version |
|---|---|
| checkout commit | `fdf0caedb301d5308ea804ff9cb9bb5b43ce73cb` |
| checkout describe | `kit-py/v0.1.8-776-gfdf0caed` |
| auth contract | `1.1.0` |
| sales contract | `1.10.1` |
| campaigns contract | `1.4.2` |
