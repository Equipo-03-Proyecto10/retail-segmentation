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
}
