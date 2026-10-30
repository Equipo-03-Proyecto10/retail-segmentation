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
            FloorLocation.ProductFromRecommendation(42)
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
    }

    @Test
    fun aSubStateBacksOutToItsRoot() {
        assertEquals(FloorLocation.CheckIn, FloorLocation.StorePicker.back)
        assertEquals(FloorLocation.Scan, FloorLocation.ProductFromRecommendation(7).back)
    }

    @Test
    fun aRootOtherThanCheckInBacksOutToCheckIn() {
        listOf(FloorLocation.Scan, FloorLocation.Sale, FloorLocation.Outbox).forEach {
            assertEquals(FloorLocation.CheckIn, it.back)
        }
    }

    @Test
    fun checkInHasNowhereToGoBack() {
        assertNull(FloorLocation.CheckIn.back)
    }
}
