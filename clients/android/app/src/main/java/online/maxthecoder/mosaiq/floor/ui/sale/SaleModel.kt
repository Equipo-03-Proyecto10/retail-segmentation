package online.maxthecoder.mosaiq.floor.ui.sale

import java.math.BigDecimal
import java.math.RoundingMode
import online.maxthecoder.mosaiq.floor.AppText
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.Customer
import online.maxthecoder.mosaiq.floor.net.ErrorEnvelope
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.net.Promotion
import online.maxthecoder.mosaiq.floor.net.Sale
import online.maxthecoder.mosaiq.floor.net.SaleLineRequest
import online.maxthecoder.mosaiq.floor.net.SaleRequest
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.outbox.asError
import online.maxthecoder.mosaiq.floor.outbox.refusalReason
import online.maxthecoder.mosaiq.floor.outbox.refusedLineIndex
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.ui.CustomerSearchState
import online.maxthecoder.mosaiq.floor.ui.DiscountState
import online.maxthecoder.mosaiq.floor.ui.InterestsState
import online.maxthecoder.mosaiq.floor.ui.RegistrationState
import online.maxthecoder.mosaiq.floor.ui.SaleBuilderState
import online.maxthecoder.mosaiq.floor.ui.SaleLine

/** Everything the sale screen draws, read once from the view model so the content is stateless. */
data class SaleUi(
    val customer: Customer? = null,
    val customers: CustomerSearchState = CustomerSearchState(),
    val registration: RegistrationState = RegistrationState(),
    val interests: InterestsState = InterestsState(),
    val sale: SaleBuilderState = SaleBuilderState(),
    val history: List<OutboxRow> = emptyList(),
    val store: ChosenStore? = null,
    val statusMessage: String? = null,
    val statusError: ApiError? = null,
    /** The queued sale's row, once "Confirm sale" was tapped: waiting, recorded or refused. */
    val queued: OutboxRow? = null,
    /** The client ids of refused sales whose cart is still kept on this phone, so they can be corrected. */
    val kept: Set<String> = emptySet()
)

/** Whether a refused sale can be put back in the cart to fix, and if not, why. */
enum class Correction {
    /** This very sale's cart is open now, to fix and confirm again. */
    OPEN,

    /** The cart is free, or it is this very sale's. */
    AVAILABLE,

    /** Another sale is being built; correcting this one would replace it. */
    BUSY,

    /** The cart is no longer on this phone: the sale can only be discarded. */
    GONE
}

/** What "Correct" can do for a refused [row]: the sale in progress is never replaced behind the seller's back. */
fun correctionOf(row: OutboxRow, ui: SaleUi): Correction {
    val sale = ui.sale
    if (row.clientId in sale.corrects) return Correction.OPEN
    if (row.clientId !in ui.kept) return Correction.GONE
    val free = sale.lines.isEmpty() && sale.queuedRowId == null && !sale.confirming && !sale.reviewing
    return if (free || sale.queuedRowId == row.id) Correction.AVAILABLE else Correction.BUSY
}

/** What the sale screen can ask for; the stateful wrapper wires each to the view model. */
class SaleActions(
    val onSearchCustomers: (query: String, email: String) -> Unit = { _, _ -> },
    val onCustomerPage: (Int) -> Unit = {},
    val onSelectCustomer: (Customer) -> Unit = {},
    val onRegister: (name: String, email: String, phone: String) -> Unit = { _, _, _ -> },
    val onChooseChannel: (Int) -> Unit = {},
    val onLoadChannels: () -> Unit = {},
    val onToggleInterest: (Int) -> Unit = {},
    val onSaveInterests: () -> Unit = {},
    val onLoadInterests: () -> Unit = {},
    val onSearchProducts: (String) -> Unit = {},
    val onAddProduct: (Product) -> Unit = {},
    val onSetQuantity: (productId: Int, quantity: Int) -> Unit = { _, _ -> },
    val onChoosePromotion: (productId: Int, promotionId: Int?) -> Unit = { _, _ -> },
    val onLoadPromotions: () -> Unit = {},
    val onReview: () -> Unit = {},
    val onCancelReview: () -> Unit = {},
    val onConfirm: () -> Unit = {},
    val onBackToCart: () -> Unit = {},
    val onNewSale: () -> Unit = {},
    val onCorrect: (OutboxRow) -> Unit = {},
    val onDiscard: (OutboxRow) -> Unit = {},
    val onRefreshStatus: (OutboxRow) -> Unit = {},
    val onPickStore: () -> Unit = {},
    val rowStatus: (OutboxRow) -> String? = { null }
)

/** The channel every floor sale is recorded under (the view model's fixed choice). */
internal const val PHYSICAL_STORE = "physical_store"

/** A listed price as a number; a price that cannot be read counts as zero. */
private fun listPrice(line: SaleLine): BigDecimal = line.product.listPrice.toBigDecimalOrNull() ?: BigDecimal.ZERO

/**
 * The discount a promotion allows here, in percent: the smaller of its own and the store's
 * `max_discount_percent`, or its own when the store sets no cap (contracts/sales 1.9.0).
 */
fun allowedPercent(promotion: Promotion, storeCap: String?): BigDecimal {
    val own = promotion.discountPercent.toBigDecimalOrNull() ?: BigDecimal.ZERO
    val cap = storeCap?.toBigDecimalOrNull() ?: return own
    return own.min(cap)
}

/** True when the store's cap, not the promotion, sets how much this promotion may take off. */
fun cappedByStore(promotion: Promotion, storeCap: String?): Boolean {
    val own = promotion.discountPercent.toBigDecimalOrNull() ?: return false
    val cap = storeCap?.toBigDecimalOrNull() ?: return false
    return cap < own
}

/**
 * The unit price charged: the list price, or with a promotion the list price times
 * (1 - percent / 100), rounded half up to the cent, which is the lowest price sales accepts.
 */
fun chargedPrice(line: SaleLine, storeCap: String? = null): BigDecimal {
    val list = listPrice(line).setScale(2, RoundingMode.HALF_UP)
    val promotion = line.promotion ?: return list
    val keep = BigDecimal.ONE - allowedPercent(promotion, storeCap).movePointLeft(2)
    return (listPrice(line) * keep).setScale(2, RoundingMode.HALF_UP).min(list)
}

/** One line's amount: the charged unit price times quantity. */
fun lineTotal(line: SaleLine, storeCap: String? = null): BigDecimal =
    chargedPrice(line, storeCap) * line.quantity.toBigDecimal()

/** The sale before discounts: list prices times quantities. */
fun saleSubtotal(sale: SaleBuilderState): BigDecimal = sale.lines.fold(BigDecimal.ZERO) { sum, line ->
    sum + listPrice(line).setScale(2, RoundingMode.HALF_UP) * line.quantity.toBigDecimal()
}

/** The sale total: the sum of its lines at the prices charged. */
fun saleTotal(sale: SaleBuilderState): BigDecimal =
    sale.lines.fold(BigDecimal.ZERO) { sum, line -> sum + lineTotal(line, sale.discounts.maxDiscountPercent) }

/** What the promotions take off the sale. */
fun saleDiscount(sale: SaleBuilderState): BigDecimal = saleSubtotal(sale) - saleTotal(sale)

/** The promotions a line may name: active, for this store and this channel. */
fun applicablePromotions(discounts: DiscountState): List<Promotion> {
    val store = discounts.storeId ?: return emptyList()
    val channel = discounts.channelId ?: return emptyList()
    return discounts.promotions.filter { it.isActive && store in it.storeIds && channel in it.channelIds }
}

/** The request lines, in cart order, at the prices charged and with the promotion each relies on. */
fun saleLines(sale: SaleBuilderState): List<SaleLineRequest> = sale.lines.map {
    SaleLineRequest(
        it.product.productId,
        it.quantity,
        chargedPrice(it, sale.discounts.maxDiscountPercent).toPlainString(),
        it.promotion?.promotionId
    )
}

/** One line of a receipt: the server's quantity and price, with the name and promotion kept on the phone. */
data class ReceiptLine(
    val productId: Int,
    val name: String?,
    val quantity: Int,
    val unitPrice: String,
    val promotion: String?
)

/** A recorded sale as the receipt shows it. [discount] is null when the phone cannot tell it. */
data class Receipt(
    val saleId: Long,
    val replayed: Boolean,
    val lines: List<ReceiptLine>,
    val discount: BigDecimal?,
    val total: String
)

/**
 * The receipt of a recorded sale: the sale the service answered, its lines named from the cart that
 * was confirmed. Null while the row is not done, or when its answer cannot be read.
 */
fun receiptOf(row: OutboxRow?, cart: List<SaleLine>): Receipt? {
    if (row?.state != RowState.DONE) return null
    val sale = row.responseBody?.let { runCatching { json.decodeFromString(Sale.serializer(), it) }.getOrNull() }
        ?: return null
    val sent = runCatching { json.decodeFromString(SaleRequest.serializer(), row.body) }.getOrNull()
    val byId = cart.associateBy { it.product.productId }
    val lines = sale.lines.map { line ->
        val kept = byId[line.productId]
        val promotionId = sent?.lines?.firstOrNull { it.productId == line.productId }?.promotionId
        ReceiptLine(
            productId = line.productId,
            name = kept?.product?.name,
            quantity = line.quantity,
            unitPrice = line.unitPrice,
            promotion = promotionId?.let { id -> kept?.promotion?.takeIf { it.promotionId == id }?.name }
        )
    }
    val listed = sale.lines.map { line -> byId[line.productId]?.product?.listPrice?.toBigDecimalOrNull() }
    val discount =
        if (listed.any { it == null }) {
            null
        } else {
            sale.lines.zip(listed).fold(BigDecimal.ZERO) { sum, (line, list) ->
                val charged = line.unitPrice.toBigDecimalOrNull() ?: list!!
                sum + (list!!.setScale(2, RoundingMode.HALF_UP) - charged).max(BigDecimal.ZERO) *
                    line.quantity.toBigDecimal()
            }
        }
    return Receipt(sale.saleId, row.code == "idempotent_replayed", lines, discount, sale.total)
}

/** Why a sale was refused, in words, and the cart line the refusal names, when it names one. */
data class SaleRefusal(val error: ApiError, val reason: String, val line: String?)

/** The refusal of a queued sale, for the seller; null unless the row was refused. */
fun refusalOf(row: OutboxRow?, cart: List<SaleLine>): SaleRefusal? {
    if (row?.state != RowState.REFUSED) return null
    val error = row.asError(AppText.get(R.string.sale_refused_default))
    val details = row.responseBody
        ?.let { runCatching { json.decodeFromString(ErrorEnvelope.serializer(), it) }.getOrNull() }
        ?.details
        .orEmpty()
    val sent = runCatching { json.decodeFromString(SaleRequest.serializer(), row.body) }.getOrNull()
    val productId = refusedLineIndex(details)?.let { sent?.lines?.getOrNull(it)?.productId }
    val line = productId?.let { id -> cart.firstOrNull { it.product.productId == id }?.product?.name }
    return SaleRefusal(error.copy(details = details), refusalReason(row.code) ?: error.message, line)
}
