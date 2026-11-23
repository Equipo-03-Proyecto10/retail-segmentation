package online.maxthecoder.mosaiq.floor.ui.sale

import java.math.BigDecimal
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.net.Promotion
import online.maxthecoder.mosaiq.floor.net.SaleLineRequest
import online.maxthecoder.mosaiq.floor.ui.DiscountState
import online.maxthecoder.mosaiq.floor.ui.SaleBuilderState
import online.maxthecoder.mosaiq.floor.ui.SaleLine
import online.maxthecoder.mosaiq.floor.ui.formatMoney
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** A line's amount and the sale total come from one place, and are shown through `formatMoney`. */
class SaleTotalsTest {
    private fun product(price: String) = Product(1, "SKU1", "Trail socks", 3, price, true)

    @Test
    fun aLineIsUnitPriceTimesQuantity() {
        assertEquals(BigDecimal("25.00"), lineTotal(SaleLine(product("12.50"), 2)))
        assertEquals("\$25.00", formatMoney(lineTotal(SaleLine(product("12.50"), 2))))
    }

    @Test
    fun aPriceThatCannotBeReadCountsAsZero() {
        assertEquals(0, BigDecimal.ZERO.compareTo(lineTotal(SaleLine(product("n/a"), 3))))
    }

    private fun promotion(percent: String) =
        Promotion(51, 12, "Winter", percent, "2026-11-01", "2026-11-30", listOf(3), listOf(3), true)

    @Test
    fun aPromotionTakesTheSmallerOfItsDiscountAndTheStoreCapRoundedHalfUp() {
        val line = SaleLine(product("129.90"), 1, promotion("20.00"))
        // No cap of the store's own: 129.90 x 0.80.
        assertEquals(BigDecimal("103.92"), chargedPrice(line, null))
        // A 15% cap is tighter: 129.90 x 0.85 = 110.415, half up to 110.42 (contracts/sales 1.9.0).
        assertEquals(BigDecimal("110.42"), chargedPrice(line, "15.00"))
        assertTrue(cappedByStore(line.promotion!!, "15.00"))
        // A looser cap leaves the promotion as the limit.
        assertEquals(BigDecimal("103.92"), chargedPrice(line, "50.00"))
        // A 0.00 cap allows no discount at all.
        assertEquals(BigDecimal("129.90"), chargedPrice(line, "0.00"))
        assertEquals(BigDecimal("12.50"), chargedPrice(SaleLine(product("12.5"), 1), "15.00"))
    }

    @Test
    fun theRequestLinesCarryTheChargedPriceAndThePromotionOnlyWhenNamed() {
        val sale =
            SaleBuilderState(
                lines = listOf(SaleLine(product("129.90"), 2, promotion("20.00")), SaleLine(product("12.5"), 1)),
                discounts = DiscountState(storeId = 3, channelId = 3, maxDiscountPercent = "15.00", loaded = true)
            )
        assertEquals(
            listOf(SaleLineRequest(1, 2, "110.42", 51), SaleLineRequest(1, 1, "12.50", null)),
            saleLines(sale)
        )
        assertEquals(BigDecimal("272.30"), saleSubtotal(sale))
        assertEquals(BigDecimal("233.34"), saleTotal(sale))
        assertEquals(BigDecimal("38.96"), saleDiscount(sale))
    }

    @Test
    fun onlyActivePromotionsForTheStoreAndChannelApply() {
        val here = promotion("10.00")
        val elsewhere = here.copy(promotionId = 52, storeIds = listOf(4))
        val online = here.copy(promotionId = 53, channelIds = listOf(1))
        val ended = here.copy(promotionId = 54, isActive = false)
        val discounts = DiscountState(3, 3, listOf(here, elsewhere, online, ended), loaded = true)
        assertEquals(listOf(here), applicablePromotions(discounts))
    }

    @Test
    fun theSaleTotalIsTheSumOfItsLines() {
        val sale = SaleBuilderState(lines = listOf(SaleLine(product("12.50"), 2), SaleLine(product("0.99"), 1)))

        assertEquals("\$25.99", formatMoney(saleTotal(sale)))
    }
}
