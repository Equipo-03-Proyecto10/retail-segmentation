package online.maxthecoder.mosaiq.floor

import online.maxthecoder.mosaiq.floor.ui.CameraPermission
import online.maxthecoder.mosaiq.floor.ui.scan.StockStatus
import online.maxthecoder.mosaiq.floor.ui.scan.canBeSold
import online.maxthecoder.mosaiq.floor.ui.scan.reconcilePermission
import online.maxthecoder.mosaiq.floor.ui.scan.stockStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ScanFormatTest {
    @Test
    fun stockHasNoLowTierBecauseTheAnswerCarriesNoThreshold() {
        assertEquals(StockStatus.OUT_OF_STOCK, stockStatus(0))
        assertEquals(StockStatus.IN_STOCK, stockStatus(1))
        assertEquals(StockStatus.NOT_STOCKED, stockStatus(null))
    }

    @Test
    fun thePhysicalStoreChannelsReasonDecidesTheBadge() {
        assertEquals(StockStatus.IN_STOCK, stockStatus(14, "AVAILABLE"))
        assertEquals(StockStatus.OUT_OF_STOCK, stockStatus(0, "OUT_OF_STOCK"))
        assertEquals(StockStatus.NOT_STOCKED, stockStatus(null, "NO_STOCK_RECORD"))
        // Stock on hand does not make a product sellable where stock-service says it is not.
        assertEquals(StockStatus.NOT_SELLABLE, stockStatus(2, "NOT_SELLABLE"))
        assertEquals(StockStatus.CHANNEL_NOT_ENABLED, stockStatus(41, "CHANNEL_NOT_ENABLED"))
    }

    @Test
    fun onlyAnActiveProductCanBeSold() {
        assertTrue(canBeSold(isActive = true, status = "active"))
        assertTrue(canBeSold(isActive = true, status = null))
        assertFalse(canBeSold(isActive = false, status = "discontinued"))
        assertFalse(canBeSold(isActive = false, status = "paused"))
        assertFalse(canBeSold(isActive = false, status = null))
    }

    @Test
    fun aRevokeTurnsGrantedIntoDeniedAndAGrantTurnsAnythingIntoGranted() {
        assertEquals(CameraPermission.DENIED, reconcilePermission(CameraPermission.GRANTED, granted = false))
        assertEquals(CameraPermission.GRANTED, reconcilePermission(CameraPermission.DENIED, granted = true))
        assertEquals(CameraPermission.GRANTED, reconcilePermission(CameraPermission.NOT_ASKED, granted = true))
        assertEquals(CameraPermission.NOT_ASKED, reconcilePermission(CameraPermission.NOT_ASKED, granted = false))
        assertEquals(CameraPermission.DENIED, reconcilePermission(CameraPermission.DENIED, granted = false))
    }
}
