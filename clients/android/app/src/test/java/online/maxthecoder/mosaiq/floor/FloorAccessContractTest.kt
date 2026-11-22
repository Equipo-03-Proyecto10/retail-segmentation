package online.maxthecoder.mosaiq.floor

import java.io.File
import online.maxthecoder.mosaiq.floor.auth.FloorAccess
import online.maxthecoder.mosaiq.floor.auth.FloorSection
import online.maxthecoder.mosaiq.floor.auth.RecordPart
import online.maxthecoder.mosaiq.floor.auth.RecordWriters
import online.maxthecoder.mosaiq.floor.auth.ServiceOperation
import online.maxthecoder.mosaiq.floor.auth.SessionRepository
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.yaml.snakeyaml.Yaml

/**
 * The one table of which sections a role holds (`FloorAccess`) against what the services say:
 * each operation's `x-mosaiq-roles` in the merged contracts, read from the repository.
 */
class FloorAccessContractTest {
    /** `<service>/<operationId>` to the roles its contract lists. */
    private val rolesByOperation: Map<String, Set<String>> by lazy { readContracts() }

    private fun readContracts(): Map<String, Set<String>> {
        // Gradle runs unit tests from clients/android/app.
        val contracts = File("../../../contracts").canonicalFile
        check(contracts.isDirectory) { "contracts/ not found at $contracts" }
        val found = mutableMapOf<String, Set<String>>()
        contracts.listFiles { f -> f.isDirectory }!!.forEach { dir ->
            val spec = File(dir, "openapi.yaml")
            if (!spec.isFile) return@forEach
            val paths = (Yaml().load<Map<String, Any?>>(spec.readText())["paths"] as? Map<*, *>) ?: return@forEach
            paths.values.forEach { item ->
                (item as? Map<*, *>)?.values?.forEach { operation ->
                    val op = operation as? Map<*, *> ?: return@forEach
                    val id = op["operationId"] as? String ?: return@forEach
                    val roles = (op["x-mosaiq-roles"] as? List<*>)?.map { it.toString() }.orEmpty()
                    found[dir.name + "/" + id] = roles.toSet()
                }
            }
        }
        return found
    }

    private fun rolesOf(service: String, operationId: String): Set<String> =
        rolesByOperation["$service/$operationId"] ?: error("contracts/$service defines no $operationId")

    @Test
    fun theFloorRolesAreTheOnesAdr0060Decides() {
        assertEquals(
            setOf("SALES_ASSOCIATE", "STORE_MANAGER", "LOYALTY_OFFICER", "COMMERCIAL_SUPERVISOR", "ADMIN"),
            SessionRepository.FLOOR_ROLES
        )
        assertEquals(SessionRepository.FLOOR_ROLES, FloorAccess.profiles.keys)
    }

    @Test
    fun everySectionIsHeldExactlyWhenItsOperationsAllListTheRole() {
        SessionRepository.FLOOR_ROLES.forEach { role ->
            FloorSection.entries.forEach { section ->
                val refused =
                    FloorAccess.operations.getValue(section)
                        .filter { role !in rolesOf(it.service, it.operationId) }
                        .map { "${it.service}/${it.operationId}" }
                assertEquals(
                    "$role and $section: held when no operation refuses it (refused: $refused)",
                    refused.isEmpty(),
                    FloorAccess.holds(role, section)
                )
            }
        }
    }

    @Test
    fun theNotificationPollAndTheIncidentReportRunForExactlyTheRolesTheirContractsList() {
        FloorAccess.background.forEach { (operation, roles) ->
            assertEquals(
                "${operation.service}/${operation.operationId}",
                SessionRepository.FLOOR_ROLES.intersect(rolesOf(operation.service, operation.operationId)),
                roles
            )
        }
        assertEquals(
            setOf("COMMERCIAL_SUPERVISOR", "ADMIN"),
            SessionRepository.FLOOR_ROLES.filter { FloorAccess.runs(it, FloorAccess.INCIDENTS_POLL) }.toSet()
        )
    }

    @Test
    fun everyOperationASectionCallsExistsInItsContract() {
        FloorAccess.operations.values.flatten().forEach { rolesOf(it.service, it.operationId) }
        FloorAccess.recordings.values.flatten().forEach { rolesOf(it.service, it.operationId) }
    }

    @Test
    fun aSectionIsRecordedExactlyWhenItIsHeldAndItsRecordingsAllListTheRole() {
        SessionRepository.FLOOR_ROLES.forEach { role ->
            FloorSection.entries.forEach { section ->
                val recordings = FloorAccess.recordings[section].orEmpty()
                val allowed = recordings.isNotEmpty() && recordings.all { role in rolesOf(it.service, it.operationId) }
                assertEquals(
                    "$role records $section",
                    FloorAccess.holds(role, section) && allowed,
                    FloorAccess.records(role, section)
                )
            }
        }
        // F27-06: the seller records what was shown and tapped; the loyalty officer only reads.
        assertTrue(FloorAccess.records("SALES_ASSOCIATE", FloorSection.CUSTOMER))
        assertTrue(!FloorAccess.records("LOYALTY_OFFICER", FloorSection.CUSTOMER))
    }

    @Test
    fun theOutboxIsEveryProfilesAndCheckInIsWhereItOpens() {
        SessionRepository.FLOOR_ROLES.forEach { role ->
            assertTrue("$role holds the outbox", FloorAccess.holds(role, FloorSection.OUTBOX))
            assertEquals("$role opens on Check-in", FloorSection.CHECK_IN, FloorAccess.start(role))
        }
    }

    @Test
    fun eachStoreProfileGetsItsOwnNavigation() {
        val everything =
            listOf(
                FloorSection.CHECK_IN,
                FloorSection.SCAN,
                FloorSection.SALE,
                FloorSection.INDICATORS,
                FloorSection.OUTBOX
            )
        assertEquals(
            listOf(FloorSection.CHECK_IN, FloorSection.CUSTOMER, FloorSection.SCAN, FloorSection.OUTBOX),
            FloorAccess.sections("SALES_ASSOCIATE")
        )
        assertEquals(everything, FloorAccess.sections("STORE_MANAGER"))
        assertEquals(
            listOf(FloorSection.CHECK_IN, FloorSection.CUSTOMER, FloorSection.CUSTOMER_RECORD, FloorSection.OUTBOX),
            FloorAccess.sections("LOYALTY_OFFICER")
        )
        assertEquals(
            listOf(FloorSection.CHECK_IN, FloorSection.REVIEW, FloorSection.OUTBOX),
            FloorAccess.sections("COMMERCIAL_SUPERVISOR")
        )
        // The floor search and floor view are the store profiles' reads: the administrator does not hold them.
        assertEquals(everything + FloorSection.REVIEW, FloorAccess.sections("ADMIN"))
    }

    @Test
    fun onlyTheLoyaltyOfficerReachesTheCustomerRecordAndTheSellerIsRefusedItsWrites() {
        assertEquals(
            setOf("LOYALTY_OFFICER"),
            FloorAccess.profiles.filterValues { FloorSection.CUSTOMER_RECORD in it }.keys
        )
        // The record opens from Customer, so whoever holds it holds Customer too.
        FloorAccess.profiles.values.filter { FloorSection.CUSTOMER_RECORD in it }
            .forEach { assertTrue(FloorSection.CUSTOMER in it) }
        // F27-09: the seller reaches a customer, but the contracts refuse it every write of the record.
        listOf(
            "customers" to "registerCustomer",
            "customer-preferences" to "recordConsentDecisions",
            "customer-preferences" to "replacePreferredChannels",
            "customer-preferences" to "replaceInterestCategories"
        ).forEach { (service, id) ->
            assertTrue("$service/$id refuses the seller", "SALES_ASSOCIATE" !in rolesOf(service, id))
            assertTrue("$service/$id admits the officer", "LOYALTY_OFFICER" in rolesOf(service, id))
        }
        assertTrue(!FloorAccess.holds("SALES_ASSOCIATE", FloorSection.CUSTOMER_RECORD))
    }

    @Test
    fun whoMayChangeEachPartOfTheRecordIsWhatItsWriteLists() {
        RecordWriters.writers.forEach { (part, writer) ->
            assertEquals(
                "$part: ${writer.operation.service}/${writer.operation.operationId}",
                rolesOf(writer.operation.service, writer.operation.operationId),
                writer.roles.toSet()
            )
        }
        assertEquals(RecordPart.entries.toSet(), RecordWriters.writers.keys)
    }

    @Test
    fun onlyTheStoreManagerAndTheAdministratorReadTheStoreIndicators() {
        val holders = SessionRepository.FLOOR_ROLES.filter { FloorAccess.holds(it, FloorSection.INDICATORS) }
        assertEquals(setOf("STORE_MANAGER", "ADMIN"), holders.toSet())
    }

    @Test
    fun onlyTheCommercialSupervisorAndTheAdministratorReviewTheFloor() {
        val holders = SessionRepository.FLOOR_ROLES.filter { FloorAccess.holds(it, FloorSection.REVIEW) }
        assertEquals(setOf("COMMERCIAL_SUPERVISOR", "ADMIN"), holders.toSet())
        // F27-14: reading the incidents and moving them forward are the operations of that section.
        listOf("listCaptureIncidents", "getCaptureIncident", "reviewCaptureIncident").forEach {
            assertEquals(it, setOf("ADMIN", "COMMERCIAL_SUPERVISOR"), rolesOf("engagement", it))
            assertTrue(
                FloorAccess.operations.getValue(FloorSection.REVIEW).contains(ServiceOperation("engagement", it))
            )
        }
        // The seller and the store manager are refused the review by the service, not only by this table.
        listOf("SALES_ASSOCIATE", "STORE_MANAGER", "LOYALTY_OFFICER").forEach {
            assertTrue("$it is refused", it !in rolesOf("engagement", "reviewCaptureIncident"))
        }
    }

    @Test
    fun everyOtherRoleTheContractsNameGetsNothing() {
        val everyRole = rolesByOperation.values.flatten().toSet()
        assertTrue("the contracts name the ten roles", everyRole.size >= 10)
        (everyRole - SessionRepository.FLOOR_ROLES).forEach { role ->
            assertTrue("$role gets no section", FloorAccess.sections(role).isEmpty())
            assertEquals(null, FloorAccess.start(role))
        }
    }

    @Test
    fun anUnknownOrMissingRoleGetsNothing() {
        listOf(null, "", "store_manager", "STORE_MANAGER ", "ROOT", "SUPERVISOR").forEach {
            assertTrue("[$it]", FloorAccess.sections(it).isEmpty())
            FloorSection.entries.forEach { section -> assertTrue(!FloorAccess.holds(it, section)) }
        }
    }
}
