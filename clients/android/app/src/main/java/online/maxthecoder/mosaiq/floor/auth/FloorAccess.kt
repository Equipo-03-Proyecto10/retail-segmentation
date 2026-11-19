package online.maxthecoder.mosaiq.floor.auth

/** The root sections of the floor shell, whoever may open them. */
enum class FloorSection { CHECK_IN, SCAN, SALE, OUTBOX, CUSTOMER }

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
            "LOYALTY_OFFICER" to listOf(FloorSection.CHECK_IN, FloorSection.CUSTOMER, FloorSection.OUTBOX),
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
                    ServiceOperation("engagement", "createEngagementEventV2")
                ),
            FloorSection.OUTBOX to emptyList(),
            // Identifying a customer by loyalty card or by a floor search (F27-02), their
            // permitted commercial profile: segment, interests and restrictions (F27-03), and
            // the recommendations judged at the checked-in store, with the names they need (F27-06).
            FloorSection.CUSTOMER to
                listOf(
                    ServiceOperation("customers", "lookupLoyaltyCard"),
                    ServiceOperation("customers", "searchFloorCustomers"),
                    ServiceOperation("customers", "getFloorCustomer"),
                    ServiceOperation("segments", "getFloorCurrentAssignment"),
                    ServiceOperation("customer-preferences", "getInterestProfile"),
                    ServiceOperation("customer-preferences", "getRestrictionProfile"),
                    ServiceOperation("categories", "listCategories"),
                    ServiceOperation("sales", "listChannels"),
                    ServiceOperation("recommendations", "getRecommendationsV2"),
                    ServiceOperation("products", "listProducts"),
                    ServiceOperation("stores", "listStores")
                )
        )

    /**
     * What a section records through the outbox on top of what it reads, and only for a role
     * that holds the section and every one of these operations. A role that holds the section
     * but not these still reads it and records nothing: a loyalty officer sees the customer's
     * recommendations, but engagement records only a seller's impressions and taps (F27-06).
     * `FloorAccessContractTest` checks [records] against the contracts, as for [operations].
     */
    val recordings: Map<FloorSection, List<ServiceOperation>> =
        mapOf(
            FloorSection.CUSTOMER to
                listOf(
                    ServiceOperation("engagement", "createEngagementEventV2")
                )
        )

    /** The roles that record what [recordings] names for a section, beyond holding it. */
    private val recorders: Map<FloorSection, Set<String>> =
        mapOf(FloorSection.CUSTOMER to setOf("SALES_ASSOCIATE"))

    /** The sections [role] gets; empty for a role the floor app is not for. */
    fun sections(role: String?): List<FloorSection> = profiles[role].orEmpty()

    fun holds(role: String?, section: FloorSection): Boolean = section in sections(role)

    /** Whether [role], in [section], records the section's [recordings] as well as reading it. */
    fun records(role: String?, section: FloorSection): Boolean =
        holds(role, section) && role in recorders[section].orEmpty()

    /** Where [role] opens, or `null` for a role that gets nothing. */
    fun start(role: String?): FloorSection? = sections(role).firstOrNull()
}
