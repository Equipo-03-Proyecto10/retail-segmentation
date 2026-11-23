package online.maxthecoder.mosaiq.floor

import online.maxthecoder.mosaiq.floor.auth.FloorSection
import online.maxthecoder.mosaiq.floor.ui.shell.FloorLocation
import online.maxthecoder.mosaiq.floor.ui.shell.FloorLocationSaver
import online.maxthecoder.mosaiq.floor.ui.shell.allowedFor
import online.maxthecoder.mosaiq.floor.ui.shell.backFor
import online.maxthecoder.mosaiq.floor.ui.shell.root
import online.maxthecoder.mosaiq.floor.ui.shell.section
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Every way into a location goes through `allowedFor`: a section the role does not hold is never the answer. */
class FloorLocationAccessTest {
    private val every =
        listOf(
            FloorLocation.CheckIn,
            FloorLocation.StorePicker,
            FloorLocation.Scan,
            FloorLocation.ProductFromRecommendation(7),
            FloorLocation.Sale,
            FloorLocation.RegisterCustomer,
            FloorLocation.Outbox
        )

    @Test
    fun aLocationOfAHeldSectionIsKept() {
        every.forEach { assertEquals(it, it.allowedFor("STORE_MANAGER")) }
        assertEquals(FloorLocation.Scan, FloorLocation.Scan.allowedFor("SALES_ASSOCIATE"))
        assertEquals(FloorLocation.StorePicker, FloorLocation.StorePicker.allowedFor("LOYALTY_OFFICER"))
    }

    @Test
    fun aLocationOfASectionNotHeldFallsToWhereTheRoleOpens() {
        listOf(FloorLocation.Sale, FloorLocation.RegisterCustomer, FloorLocation.ProductFromRecommendation(7))
            .forEach { assertEquals(FloorLocation.CheckIn, it.allowedFor("LOYALTY_OFFICER")) }
        assertEquals(FloorLocation.CheckIn, FloorLocation.Sale.allowedFor("SALES_ASSOCIATE"))
        assertEquals(FloorLocation.CheckIn, FloorLocation.Scan.allowedFor("COMMERCIAL_SUPERVISOR"))
    }

    @Test
    fun theReviewIsKeptForTheSupervisorAndTheAdministratorAndForNobodyElse() {
        listOf("COMMERCIAL_SUPERVISOR", "ADMIN").forEach {
            assertEquals(FloorLocation.Review, FloorLocation.Review.allowedFor(it))
        }
        listOf("SALES_ASSOCIATE", "STORE_MANAGER", "LOYALTY_OFFICER").forEach {
            assertEquals(FloorLocation.CheckIn, FloorLocation.Review.allowedFor(it))
        }
        // A restored "review" is only a request too.
        assertEquals(FloorLocation.CheckIn, FloorLocationSaver.restore("review")!!.allowedFor("STORE_MANAGER"))
    }

    @Test
    fun noLocationIsAllowedForARoleThatHoldsNothing() {
        listOf(null, "ANALYST", "CUSTOMER", "whatever").forEach { role ->
            every.forEach { assertEquals(FloorLocation.CheckIn, it.allowedFor(role)) }
        }
    }

    @Test
    fun aRestoredSavedStateIsOnlyARequest() {
        listOf("sale", "register_customer", "product:12", "scan").forEach { saved ->
            val restored = FloorLocationSaver.restore(saved)!!
            val shown = restored.allowedFor("LOYALTY_OFFICER")
            assertEquals(FloorSection.CHECK_IN, shown.section)
        }
    }

    @Test
    fun backNeverLeadsToASectionNotHeld() {
        every.forEach { here ->
            listOf("SALES_ASSOCIATE", "LOYALTY_OFFICER", "COMMERCIAL_SUPERVISOR").forEach { role ->
                val back = here.allowedFor(role).backFor(role)
                if (back != null) assertEquals(back, back.allowedFor(role))
            }
        }
        assertNull(FloorLocation.CheckIn.backFor("LOYALTY_OFFICER"))
        assertEquals(FloorLocation.CheckIn, FloorLocation.Outbox.backFor("LOYALTY_OFFICER"))
    }

    @Test
    fun everySectionHasOneRootAndBack() {
        // Every bar section is its root's; the customer record has no root of its own and lives in Customer.
        FloorSection.entries.filter { it != FloorSection.CUSTOMER_RECORD }.forEach { assertEquals(it, it.root.section) }
        assertEquals(FloorLocation.Customer, FloorSection.CUSTOMER_RECORD.root)
        assertEquals(FloorSection.CUSTOMER_RECORD, FloorLocation.NewCustomer.section)
        assertEquals(FloorSection.CUSTOMER_RECORD, FloorLocation.CustomerRecord("c").section)
    }

    @Test
    fun onlyTheLoyaltyOfficerReachesTheRecordOrRegistration() {
        listOf(FloorLocation.NewCustomer, FloorLocation.CustomerRecord("c")).forEach {
            assertEquals(it, it.allowedFor("LOYALTY_OFFICER"))
            listOf("SALES_ASSOCIATE", "STORE_MANAGER", "ADMIN").forEach { role ->
                assertEquals(role, FloorLocation.CheckIn, it.allowedFor(role))
            }
        }
        assertEquals(FloorLocation.CustomerProfile("c"), FloorLocation.CustomerRecord("c").backFor("LOYALTY_OFFICER"))
    }
}
