package online.maxthecoder.mosaiq.floor.ui.sale

import java.math.BigDecimal
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.Customer
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.ui.CustomerSearchState
import online.maxthecoder.mosaiq.floor.ui.InterestsState
import online.maxthecoder.mosaiq.floor.ui.RegistrationState
import online.maxthecoder.mosaiq.floor.ui.SaleBuilderState

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
    /** The chosen customer's segment in words, once insights know it. */
    val segmentLabel: String? = null,
    val segmentLoading: Boolean = false
)

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
    val onConfirm: () -> Unit = {},
    val onNewSale: () -> Unit = {},
    val onRefreshStatus: (OutboxRow) -> Unit = {},
    val onPickStore: () -> Unit = {},
    val rowStatus: (OutboxRow) -> String? = { null }
)

/** The channel every floor sale is recorded under (the view model's fixed choice). */
internal const val PHYSICAL_STORE = "physical_store"

/** The sale total: unit price times quantity, lines whose price cannot be read count as zero. */
fun saleTotal(sale: SaleBuilderState): BigDecimal = sale.lines.fold(BigDecimal.ZERO) { sum, line ->
    sum + (line.product.listPrice.toBigDecimalOrNull() ?: BigDecimal.ZERO) * line.quantity.toBigDecimal()
}
