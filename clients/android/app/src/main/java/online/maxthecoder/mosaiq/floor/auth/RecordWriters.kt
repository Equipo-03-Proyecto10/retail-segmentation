package online.maxthecoder.mosaiq.floor.auth

/** The parts of a customer's record a loyalty officer opens, each changed by its own operation. */
enum class RecordPart { CONSENT, PREFERRED_CHANNELS, INTERESTS }

/** Who may change one part of the record, as the operation that changes it lists them. */
data class RecordWriter(
    val operation: ServiceOperation,
    val roles: List<String>
)

/**
 * Who may change each part of a customer's record (F27-09), shown beside the part so the
 * officer can tell the customer who to ask. The roles are the `x-mosaiq-roles` of the write,
 * copied here because the app cannot read the contracts at run time; `RecordWritersContractTest`
 * fails the build when a contract and this table disagree. The services refuse any other role.
 */
object RecordWriters {
    val writers: Map<RecordPart, RecordWriter> =
        mapOf(
            RecordPart.CONSENT to
                RecordWriter(
                    ServiceOperation("customer-preferences", "recordConsentDecisions"),
                    listOf("LOYALTY_OFFICER", "ADMIN")
                ),
            RecordPart.PREFERRED_CHANNELS to
                RecordWriter(
                    ServiceOperation("customer-preferences", "replacePreferredChannels"),
                    listOf("LOYALTY_OFFICER", "STORE_MANAGER", "ADMIN")
                ),
            RecordPart.INTERESTS to
                RecordWriter(
                    ServiceOperation("customer-preferences", "replaceInterestCategories"),
                    listOf("LOYALTY_OFFICER", "STORE_MANAGER", "ADMIN")
                )
        )

    fun rolesFor(part: RecordPart): List<String> = writers.getValue(part).roles
}
