package online.maxthecoder.mosaiq.floor.ui.scan

import java.text.NumberFormat
import java.util.Locale

/** A product price as the floor reads it: "$12.50". A value that is not a number is shown as sent. */
fun formatPrice(raw: String): String {
    val value = raw.trim().toBigDecimalOrNull() ?: return raw
    return NumberFormat.getCurrencyInstance(Locale.US).format(value)
}

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
