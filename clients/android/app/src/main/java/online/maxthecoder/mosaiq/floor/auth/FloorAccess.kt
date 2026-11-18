package online.maxthecoder.mosaiq.floor.auth

/** The four root sections of the floor shell, whoever may open them. */
enum class FloorSection { CHECK_IN, SCAN, SALE, OUTBOX }

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
 * Today the seller does not hold Sale: customers-service, segments-service and
 * recommendations-service do not yet list `SALES_ASSOCIATE` for the customer reads that
 * flow needs (F21-13). When they do, the contract test fails and Sale moves into the
 * seller's row here.
 */
object FloorAccess {
    private val EVERYTHING = listOf(FloorSection.CHECK_IN, FloorSection.SCAN, FloorSection.SALE, FloorSection.OUTBOX)

    /** The profile of each role, in the order its bottom bar shows; the first is where it opens. */
    val profiles: Map<String, List<FloorSection>> =
        mapOf(
            "SALES_ASSOCIATE" to listOf(FloorSection.CHECK_IN, FloorSection.SCAN, FloorSection.OUTBOX),
            "STORE_MANAGER" to EVERYTHING,
            "LOYALTY_OFFICER" to listOf(FloorSection.CHECK_IN, FloorSection.OUTBOX),
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
                    ServiceOperation("engagement", "createEngagementEvent"),
                    ServiceOperation("campaigns", "listPromotions"),
                    ServiceOperation("experimentation", "getCurrentAssignment"),
                    ServiceOperation("engagement", "createEngagementEventV2")
                ),
            FloorSection.OUTBOX to emptyList()
        )

    /** The notification poll's campaign read (ADR-0071). */
    val PROMOTIONS_POLL = ServiceOperation("campaigns", "listPromotions")

    /** The notification poll's incident read, for the commercial supervisor (ADR-0060, ADR-0071). */
    val INCIDENTS_POLL = ServiceOperation("engagement", "listCaptureIncidents")

    /** The report a finally refused write queues (ADR-0071). */
    val INCIDENT_REPORT = ServiceOperation("engagement", "reportCaptureIncident")

    /**
     * What runs outside any section, and for which floor roles: the notification poll's reads and the
     * capture-incident report. `FloorAccessContractTest` checks each set against the operation's
     * `x-mosaiq-roles`.
     */
    val background: Map<ServiceOperation, Set<String>> =
        mapOf(
            PROMOTIONS_POLL to profiles.keys,
            INCIDENTS_POLL to setOf("COMMERCIAL_SUPERVISOR", "ADMIN"),
            INCIDENT_REPORT to profiles.keys
        )

    /** Whether [role] runs [operation] in the background. */
    fun runs(role: String?, operation: ServiceOperation): Boolean = role in background[operation].orEmpty()

    /** The sections [role] gets; empty for a role the floor app is not for. */
    fun sections(role: String?): List<FloorSection> = profiles[role].orEmpty()

    fun holds(role: String?, section: FloorSection): Boolean = section in sections(role)

    /** Where [role] opens, or `null` for a role that gets nothing. */
    fun start(role: String?): FloorSection? = sections(role).firstOrNull()
}
