package online.maxthecoder.mosaiq.floor.net

/**
 * The one place a service's path is built. Each prefix is `api/<name>/v1/`,
 * where `<name>` is the service that `contracts/registry.yaml` registers the
 * resource to (ADR-0041): the app calls a resource only at its owner.
 */
object Services {
    /** auth owns `sessions` and `jwks.json`. */
    const val AUTH = "api/auth/v1/"

    /** users owns `users`, `roles` and `administrator-transfers`. */
    const val USERS = "api/users/v1/"

    /** stores owns `stores`. */
    const val STORES = "api/stores/v1/"

    /** check-ins owns `check-ins`. */
    const val CHECK_INS = "api/check-ins/v1/"

    /** products owns `products`. */
    const val PRODUCTS = "api/products/v1/"

    /** stock owns `stock-levels`. */
    const val STOCK = "api/stock/v1/"

    /** customers owns `customers`. */
    const val CUSTOMERS = "api/customers/v1/"

    /** customer-preferences owns `preference-profiles`. */
    const val CUSTOMER_PREFERENCES = "api/customer-preferences/v1/"

    /** categories owns `categories`. */
    const val CATEGORIES = "api/categories/v1/"

    /** sales owns `sales` and `channels`. */
    const val SALES = "api/sales/v1/"

    /** engagement owns `engagement-events` and `engagement-summaries`. */
    const val ENGAGEMENT = "api/engagement/v1/"

    /** segments owns `segments` and `segment-assignments`. */
    const val SEGMENTS = "api/segments/v1/"

    /** recommendations owns `recommendations`. */
    const val RECOMMENDATIONS = "api/recommendations/v1/"
}
