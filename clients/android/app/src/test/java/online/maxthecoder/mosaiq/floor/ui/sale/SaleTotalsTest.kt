package online.maxthecoder.mosaiq.floor.ui.sale

import java.math.BigDecimal
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.ui.SaleBuilderState
import online.maxthecoder.mosaiq.floor.ui.SaleLine
import online.maxthecoder.mosaiq.floor.ui.formatMoney
import org.junit.Assert.assertEquals
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

    @Test
    fun theSaleTotalIsTheSumOfItsLines() {
        val sale = SaleBuilderState(lines = listOf(SaleLine(product("12.50"), 2), SaleLine(product("0.99"), 1)))

        assertEquals("\$25.99", formatMoney(saleTotal(sale)))
    }
}
