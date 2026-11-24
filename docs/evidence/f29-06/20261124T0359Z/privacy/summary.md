# F29-06 Privacidad

Started 2026-11-24T03:59:30Z, finished 2026-11-24T04:01:43Z.
2 checks, 2 held, 0 did not.

- **target:** `https://127.0.0.1`
- **command:** `-m pytest -q -rs -p no:cacheprovider --confcutdir=tests/negative --junitxml=docs/evidence/f29-06/20261124T0359Z/privacy/junit.xml tests/negative/test_privacy.py tests/negative/test_roles.py tests/negative/test_store_scope.py`

| Criterion | Case | Expected | Actual | Evidence | |
|---|---|---|---|---|---|
| Privacidad | pytest test_privacy.py, test_roles.py, test_store_scope.py | exit 0, no failure or error | exit 0, {'tests': 53, 'failures': 0, 'errors': 0, 'skipped': 1} | [1](pytest.log), [2](junit.xml) | ✓ |
| Privacidad | every F18-04 case of the run held | 1369 of 1369 | 1369 of 1369 | [1](negative-evidence.md) | ✓ |

## Versions

| Piece | Version |
|---|---|
| checkout commit | `(unavailable)` |
| checkout describe | `(unavailable)` |
| auth contract | `1.1.0` |
| users contract | `1.4.0` |
| products contract | `1.4.0` |
| categories contract | `1.2.0` |
| stores contract | `1.4.0` |
| stock contract | `1.3.0` |
| customers contract | `1.8.1` |
| customer-preferences contract | `1.1.2` |
| sales contract | `1.10.1` |
| segments contract | `1.6.2` |
| recommendations contract | `2.0.4` |
| engagement contract | `2.5.0` |
| check-ins contract | `1.4.0` |
| campaigns contract | `1.4.2` |
| audit-trail contract | `1.5.0` |
| rfm-engine contract | `1.2.1` |
| model-registry contract | `1.3.0` |
| experimentation contract | `1.2.1` |
| analytics-kpis contract | `1.2.0` |
| auth release | `/opt/mosaiq/services/auth/releases/0.1.4` |
| users release | `/opt/mosaiq/services/users/releases/0.2.0` |
| products release | `/opt/mosaiq/services/products/releases/0.3.0` |
| categories release | `/opt/mosaiq/services/categories/releases/0.1.9` |
| stores release | `/opt/mosaiq/services/stores/releases/0.2.0` |
| stock release | `/opt/mosaiq/services/stock/releases/0.3.0` |
| customers release | `/opt/mosaiq/services/customers/releases/0.4.0` |
| customer-preferences release | `/opt/mosaiq/services/customer-preferences/releases/0.3.0` |
| sales release | `/opt/mosaiq/services/sales/releases/0.7.0` |
| segments release | `/opt/mosaiq/services/segments/releases/0.6.0` |
| recommendations release | `/opt/mosaiq/services/recommendations/releases/0.5.0` |
| engagement release | `/opt/mosaiq/services/engagement/releases/0.4.0` |
| check-ins release | `/opt/mosaiq/services/check-ins/releases/0.3.0` |
| campaigns release | `/opt/mosaiq/services/campaigns/releases/0.4.0` |
| audit-trail release | `/opt/mosaiq/services/audit-trail/releases/0.1.5` |
| rfm-engine release | `/opt/mosaiq/services/rfm-engine/releases/0.8.0` |
| model-registry release | `/opt/mosaiq/services/model-registry/releases/0.5.0` |
| experimentation release | `/opt/mosaiq/services/experimentation/releases/0.8.1` |
| analytics-kpis release | `/opt/mosaiq/services/analytics-kpis/releases/0.4.0` |
| web commit | `50bf7d5c48336a39f89f34f9bb8d44a78057fb06` |
