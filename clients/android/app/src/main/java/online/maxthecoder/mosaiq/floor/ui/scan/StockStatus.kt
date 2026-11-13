package online.maxthecoder.mosaiq.floor.ui.scan

/** How the stock at the checked-in store reads on the badge. */
enum class StockStatus { IN_STOCK, OUT_OF_STOCK, NOT_STOCKED }

/**
 * The answer carries a quantity and no low-stock threshold, so there is no "Low"
 * state: zero is out of stock, anything above is in stock, and no inventory row
 * at all means the store does not hold the product.
 */
fun stockStatus(quantity: Int?): StockStatus = when {
    quantity == null -> StockStatus.NOT_STOCKED
    quantity <= 0 -> StockStatus.OUT_OF_STOCK
    else -> StockStatus.IN_STOCK
}
