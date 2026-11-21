package online.maxthecoder.mosaiq.floor.ui.scan

/** How the stock at the checked-in store reads on the badge. */
enum class StockStatus { IN_STOCK, OUT_OF_STOCK, NOT_STOCKED, NOT_SELLABLE, CHANNEL_NOT_ENABLED }

/**
 * The answer carries a quantity and no low-stock threshold, so there is no "Low"
 * state. With the physical-store channel's availability `reason` (contracts/stock),
 * the badge says what stock-service decided for that channel. Without it, zero is
 * out of stock, anything above is in stock, and no inventory row at all means the
 * store does not hold the product.
 */
fun stockStatus(quantity: Int?, reason: String? = null): StockStatus = when (reason) {
    "AVAILABLE" -> StockStatus.IN_STOCK
    "OUT_OF_STOCK" -> StockStatus.OUT_OF_STOCK
    "NO_STOCK_RECORD" -> StockStatus.NOT_STOCKED
    "NOT_SELLABLE" -> StockStatus.NOT_SELLABLE
    "CHANNEL_NOT_ENABLED" -> StockStatus.CHANNEL_NOT_ENABLED
    else ->
        when {
            quantity == null -> StockStatus.NOT_STOCKED
            quantity <= 0 -> StockStatus.OUT_OF_STOCK
            else -> StockStatus.IN_STOCK
        }
}

/** A product can go into a sale only while its commercial status is `active` (contracts/products). */
fun canBeSold(isActive: Boolean, status: String?): Boolean = isActive && (status == null || status == "active")
