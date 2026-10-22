# #414 (F16-03): the desktop customers screen against the web, same database

Observed on the local stack (gateway `127.0.0.1:8080`, web `127.0.0.1:8000`), signed in as the seeded
`ADMIN`. Customer ids are `00000000-0000-0000-0000-0000000000NN`.

| | Customer 03 (recommended) | Customer 04 (`none_match`) |
|---|---|---|
| Segment, desktop | assigned, Champion (CHAMPION), R 4 F 4 M 5, run 31, since 2026-10-22T19:13:18Z | assigned, At risk (AT_RISK), R 2 F 5 M 5, run 31, since 2026-10-22T19:13:18Z |
| Segment, web | Champion, R 4 F 4 M 5, run #31 | At risk, run #31 |
| Recommendations, desktop | `recommended`, store 17, Demo Product 2 (Bakery, 29), 1 (Dairy, 28), 3 (Snacks, 30), 4 (Beverages, 31), 5 (Household cleaning, 32); each "segment" with 3, 2, 2, 2, 2 buyers | `none_match`, no items |
| Recommendations, web | the same five products, stock and buyers, store 17 | "No eligible products" |
| Preferred channels | desktop marketplace, call_center; web call_center, marketplace | desktop mobile_app, call_center; web call_center, mobile_app |
| Interest categories | Frozen, Fruit and vegetables (both) | both: Meat and fish, Small electronics |

One difference in meaning, not in data: the web prints "since 2026-09-23" for customer 03, the date the
customer entered the label across consecutive runs (`label_since`); the desktop prints the contract's
`since`, the start of the open assignment row (2026-10-22). For customer 04 the two coincide because run
30 held another label.

Screenshots: `414-desktop-customer-03-recommended.png`, `414-desktop-customer-04-none-match.png`,
`414-web-customer-03-profile.png`, `414-web-customer-03-recommendations.png`,
`414-web-customer-04-profile.png`, `414-web-customer-04-recommendations.png`.

Writes through the screen's own operations on customer 04, all restored: the channel set re-sent
unchanged, the category set cleared with an empty list then restored (9, 10), a `PATCH` of the name to
its current value, and a forced registration channel 99 answered `unknown_channel`.
