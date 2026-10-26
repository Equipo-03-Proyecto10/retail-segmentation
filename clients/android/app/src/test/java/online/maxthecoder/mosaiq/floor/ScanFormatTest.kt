package online.maxthecoder.mosaiq.floor

import online.maxthecoder.mosaiq.floor.ui.CameraPermission
import online.maxthecoder.mosaiq.floor.ui.scan.StockStatus
import online.maxthecoder.mosaiq.floor.ui.scan.formatPrice
import online.maxthecoder.mosaiq.floor.ui.scan.reconcilePermission
import online.maxthecoder.mosaiq.floor.ui.scan.stockStatus
import org.junit.Assert.assertEquals
import org.junit.Test

class ScanFormatTest {
    @Test
    fun pricesReadAsCurrency() {
        assertEquals("$12.50", formatPrice("12.5"))
        assertEquals("$12.50", formatPrice("12.500000"))
        assertEquals("$1,234.00", formatPrice("1234"))
        assertEquals("n/a", formatPrice("n/a"))
    }

    @Test
    fun stockHasNoLowTierBecauseTheAnswerCarriesNoThreshold() {
        assertEquals(StockStatus.OUT_OF_STOCK, stockStatus(0))
        assertEquals(StockStatus.IN_STOCK, stockStatus(1))
        assertEquals(StockStatus.NOT_STOCKED, stockStatus(null))
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
