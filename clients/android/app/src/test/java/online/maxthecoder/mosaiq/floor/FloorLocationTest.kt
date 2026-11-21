package online.maxthecoder.mosaiq.floor

import androidx.compose.runtime.saveable.SaverScope
import online.maxthecoder.mosaiq.floor.ui.shell.FloorLocation
import online.maxthecoder.mosaiq.floor.ui.shell.FloorLocationSaver
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class FloorLocationTest {
    private val everyLocation =
        listOf(
            FloorLocation.CheckIn,
            FloorLocation.Scan,
            FloorLocation.Sale,
            FloorLocation.Outbox,
            FloorLocation.StorePicker,
            FloorLocation.RegisterCustomer,
            FloorLocation.ProductFromRecommendation(42),
            FloorLocation.Customer,
            FloorLocation.CustomerProfile("7b3ad781-c5de-4f40-ae50-1fa825ce2b25"),
            FloorLocation.NewCustomer,
            FloorLocation.CustomerRecord("7b3ad781-c5de-4f40-ae50-1fa825ce2b25")
        )

    @Test
    fun theSaverRoundTripsEveryLocation() {
        val scope = SaverScope { true }
        everyLocation.forEach { location ->
            val saved = with(FloorLocationSaver) { scope.save(location) }!!
            assertEquals(location, FloorLocationSaver.restore(saved))
        }
    }

    @Test
    fun anUnreadableSavedValueLandsOnTheStartOrScan() {
        assertEquals(FloorLocation.CheckIn, FloorLocationSaver.restore("nonsense"))
        assertEquals(FloorLocation.Scan, FloorLocationSaver.restore("product:x"))
        assertEquals(FloorLocation.Customer, FloorLocationSaver.restore("customer: "))
        assertEquals(FloorLocation.Customer, FloorLocationSaver.restore("record:"))
    }

    @Test
    fun aSubStateBacksOutToItsRoot() {
        assertEquals(FloorLocation.CheckIn, FloorLocation.StorePicker.back)
        assertEquals(FloorLocation.Sale, FloorLocation.RegisterCustomer.back)
        assertEquals(FloorLocation.Scan, FloorLocation.ProductFromRecommendation(7).back)
        assertEquals(FloorLocation.Customer, FloorLocation.CustomerProfile("c").back)
        assertEquals(FloorLocation.Customer, FloorLocation.NewCustomer.back)
        // The record returns to the profile it was opened from.
        assertEquals(FloorLocation.CustomerProfile("c"), FloorLocation.CustomerRecord("c").back)
    }

    @Test
    fun aRootOtherThanCheckInBacksOutToCheckIn() {
        listOf(FloorLocation.Scan, FloorLocation.Sale, FloorLocation.Outbox, FloorLocation.Customer).forEach {
            assertEquals(FloorLocation.CheckIn, it.back)
        }
    }

    @Test
    fun checkInHasNowhereToGoBack() {
        assertNull(FloorLocation.CheckIn.back)
    }
}
