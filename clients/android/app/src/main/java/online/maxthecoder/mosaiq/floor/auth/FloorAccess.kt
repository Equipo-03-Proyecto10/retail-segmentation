package online.maxthecoder.mosaiq.floor.auth

/**
 * The sections of the floor shell, whoever may open them. Each is a root of the bottom bar except
 * [CUSTOMER_RECORD], which lives inside Customer: it has no bar entry and is reached from there.
 */
enum class FloorSection { CHECK_IN, SCAN, SALE, OUTBOX, CUSTOMER, CUSTOMER_RECORD }

/** One operation of a service contract, as `operationId` in `contracts/<service>/openapi.yaml`. */
data class ServiceOperation(
    val service: String,
    val operationId: String
)

/**
 * Which sections each role gets, and the one place that says so (ADR-0060, F27-01).
 *
 * A role holds a section when every operation the section calls lists the role in its
 * `x-mosaiq-roles`; [operations] names those calls and `FloorAccessContractTest` checks
 * this table against the contracts, so a contract that widens or narrows a role fails
 * the build until the table follows. The services refuse a role anyway: this table only
 * keeps the app from offering what would be refused. A role that is not a key of
 * [profiles] gets nothing, and nothing is the answer for a code this build does not know
 * (ADR-0076).
 *
 * Today the seller does not hold Sale: that flow reads the full customer directory, the
 * full current assignment with its R, F and M scores and the preference profile with its
 * preferred channels, none of which ADR-0060 gives a seller. The seller's and the loyalty
 * officer's view of a customer is Customer: the floor view of the customer, of their segment
 * (`getFloorCurrentAssignment`, F21-26) and of their interests and restrictions (F27-03).
 */
object FloorAccess {
    private val EVERYTHING = listOf(FloorSection.CHECK_IN, FloorSection.SCAN, FloorSection.SALE, FloorSection.OUTBOX)

    /** The profile of each role, in the order its bottom bar shows; the first is where it opens. */
    val profiles: Map<String, List<FloorSection>> =
        mapOf(
            "SALES_ASSOCIATE" to
                listOf(FloorSection.CHECK_IN, FloorSection.CUSTOMER, FloorSection.SCAN, FloorSection.OUTBOX),
            "STORE_MANAGER" to EVERYTHING,
            "LOYALTY_OFFICER" to
                listOf(FloorSection.CHECK_IN, FloorSection.CUSTOMER, FloorSection.CUSTOMER_RECORD, FloorSection.OUTBOX),
            "COMMERCIAL_SUPERVISOR" to listOf(FloorSection.CHECK_IN, FloorSection.OUTBOX),
            // Not confined to stores, and holds every operation the floor calls (ADR-0060).
            "ADMIN" to EVERYTHING
        )

    /**
     * What each section calls on a service, directly or through the outbox. Outbox only
     * lists the signed-in user's own queue from the phone, so it calls nothing.
     */
    val operations: Map<FloorSection, List<ServiceOperation>> =
        mapOf(
            FloorSection.CHECK_IN to
                listOf(
                    ServiceOperation("stores", "listStores"),
                    ServiceOperation("check-ins", "createCheckIn")
                ),
            FloorSection.SCAN to
                listOf(
                    ServiceOperation("products", "getProduct"),
                    ServiceOperation("stock", "getStockLevel"),
                    ServiceOperation("engagement", "createEngagementEvent")
                ),
            FloorSection.SALE to
                listOf(
                    ServiceOperation("customers", "searchCustomers"),
                    ServiceOperation("customers", "registerCustomer"),
                    ServiceOperation("customer-preferences", "getPreferenceProfile"),
                    ServiceOperation("customer-preferences", "replaceInterestCategories"),
                    ServiceOperation("products", "listProducts"),
                    ServiceOperation("categories", "listCategories"),
                    ServiceOperation("sales", "listChannels"),
                    ServiceOperation("sales", "recordSale"),
                    ServiceOperation("sales", "getSale"),
                    ServiceOperation("segments", "getCurrentAssignment"),
                    ServiceOperation("recommendations", "getRecommendations"),
                    ServiceOperation("engagement", "createEngagementEvent")
                ),
            FloorSection.OUTBOX to emptyList(),
            // Identifying a customer by loyalty card or by a floor search (F27-02), and their
            // permitted commercial profile: segment, interests and restrictions (F27-03).
            FloorSection.CUSTOMER to
                listOf(
                    ServiceOperation("customers", "lookupLoyaltyCard"),
                    ServiceOperation("customers", "searchFloorCustomers"),
                    ServiceOperation("customers", "getFloorCustomer"),
                    ServiceOperation("segments", "getFloorCurrentAssignment"),
                    ServiceOperation("customer-preferences", "getInterestProfile"),
                    ServiceOperation("customer-preferences", "getRestrictionProfile"),
                    ServiceOperation("categories", "listCategories"),
                    ServiceOperation("sales", "listChannels")
                ),
            // Registering a customer with their consent, and their record: consent, preferences and
            // history (F27-09). It opens from the Customer section, so it calls that section's
            // operations too, and only a role that can reach a customer there holds it.
            FloorSection.CUSTOMER_RECORD to
                listOf(
                    ServiceOperation("customers", "lookupLoyaltyCard"),
                    ServiceOperation("customers", "searchFloorCustomers"),
                    ServiceOperation("customers", "getFloorCustomer"),
                    ServiceOperation("customers", "registerCustomer"),
                    ServiceOperation("sales", "listChannels"),
                    ServiceOperation("categories", "listCategories"),
                    ServiceOperation("customer-preferences", "getConsentState"),
                    ServiceOperation("customer-preferences", "recordConsentDecisions"),
                    ServiceOperation("customer-preferences", "listConsentDecisions"),
                    ServiceOperation("customer-preferences", "getPreferenceProfile"),
                    ServiceOperation("customer-preferences", "replacePreferredChannels"),
                    ServiceOperation("customer-preferences", "replaceInterestCategories"),
                    ServiceOperation("customer-preferences", "listPreferenceChanges")
                )
        )

    /** The sections [role] gets; empty for a role the floor app is not for. */
    fun sections(role: String?): List<FloorSection> = profiles[role].orEmpty()

    fun holds(role: String?, section: FloorSection): Boolean = section in sections(role)

    /** Where [role] opens, or `null` for a role that gets nothing. */
    fun start(role: String?): FloorSection? = sections(role).firstOrNull()
}
