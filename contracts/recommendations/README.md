# recommendations contract

A recommendation item carries ids and figures only: `product_id`,
`category_id`, `quantity_on_hand` and `reasons`. It has no product name, SKU,
price or category name. Fetch names with `GET /v1/products?ids=` and category
names from the categories contract; the service does not compose them
(ADR-0035).

`quantity_on_hand` is the stock held at the customer's usual store when the
result was computed (E-06). A cached result may be up to 120 s behind; the
exact bound is in `info.description` of `openapi.yaml`.

When `status` is not `recommended`, `items` is empty and `status` says why.
