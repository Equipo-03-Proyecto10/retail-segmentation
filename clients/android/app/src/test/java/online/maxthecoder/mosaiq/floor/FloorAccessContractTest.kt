package online.maxthecoder.mosaiq.floor

import java.io.File
import online.maxthecoder.mosaiq.floor.auth.FloorAccess
import online.maxthecoder.mosaiq.floor.auth.FloorSection
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
    fun everyOperationASectionCallsExistsInItsContract() {
        FloorAccess.operations.values.flatten().forEach { rolesOf(it.service, it.operationId) }
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
        assertEquals(
            listOf(FloorSection.CHECK_IN, FloorSection.SCAN, FloorSection.OUTBOX),
            FloorAccess.sections("SALES_ASSOCIATE")
        )
        assertEquals(FloorSection.entries, FloorAccess.sections("STORE_MANAGER"))
        assertEquals(listOf(FloorSection.CHECK_IN, FloorSection.OUTBOX), FloorAccess.sections("LOYALTY_OFFICER"))
        assertEquals(listOf(FloorSection.CHECK_IN, FloorSection.OUTBOX), FloorAccess.sections("COMMERCIAL_SUPERVISOR"))
        assertEquals(FloorSection.entries, FloorAccess.sections("ADMIN"))
    }

    @Test
    fun onlyTheStoreManagerAndTheAdministratorReadTheStoreIndicators() {
        val holders = SessionRepository.FLOOR_ROLES.filter { FloorAccess.holds(it, FloorSection.INDICATORS) }
        assertEquals(setOf("STORE_MANAGER", "ADMIN"), holders.toSet())
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
