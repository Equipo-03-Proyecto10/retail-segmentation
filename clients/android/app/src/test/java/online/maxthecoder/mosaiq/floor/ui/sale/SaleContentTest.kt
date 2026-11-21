package online.maxthecoder.mosaiq.floor.ui.sale

import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertTextEquals
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performImeAction
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import online.maxthecoder.mosaiq.floor.FloorTestApp
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.Channel
import online.maxthecoder.mosaiq.floor.net.Customer
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.net.Promotion
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.ui.CustomerSearchState
import online.maxthecoder.mosaiq.floor.ui.DiscountState
import online.maxthecoder.mosaiq.floor.ui.RegistrationState
import online.maxthecoder.mosaiq.floor.ui.SaleBuilderState
import online.maxthecoder.mosaiq.floor.ui.SaleLine
import online.maxthecoder.mosaiq.floor.ui.SalesContent
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

private fun hasRole(role: Role) =
    SemanticsMatcher.expectValue(androidx.compose.ui.semantics.SemanticsProperties.Role, role)

@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class SaleContentTest {
    @get:Rule val compose = createComposeRule()

    private val store = ChosenStore(1, "Centro")
    private val ana = Customer("c1", "Ana Torres", "ana@example.com", null, 1, "2025-01-01")
    private val socks = Product(1, "SKU1", "Trail socks", 3, "12.5", true)
    private val channels = listOf(Channel(1, "physical_store"), Channel(2, "online"))
    private val ready = RegistrationState(channels = channels, selectedChannelId = 1)

    private fun show(ui: () -> SaleUi, actions: SaleActions = SaleActions()) = compose.setContent {
        MosaiqTheme(darkTheme = false) { SalesContent(ui(), actions, SnackbarHostState()) {} }
    }

    @Test
    fun anEmptySaleSaysWhyItCannotBeRecorded() {
        show({ SaleUi(store = store, registration = ready) })
        compose.onNodeWithText("No items yet").assertExists()
        compose.onNodeWithTag("record-sale").assertIsNotEnabled()
        compose.onNodeWithText("Choose a customer to record the sale.").assertExists()
        compose.onNodeWithText("In store").assertExists()
        compose.onNodeWithText("physical_store", substring = true).assertDoesNotExist()
        compose.onNodeWithTag("sale-total").assertTextEquals("$0.00")
    }

    @Test
    fun withoutAStoreTheBarAsksForACheckIn() {
        show({ SaleUi(registration = ready) })
        compose.onNodeWithText("Check in at your store to record a sale.").assertExists()
        compose.onNodeWithTag("record-sale").assertIsNotEnabled()
    }

    @Test
    fun aCustomerAndItemsEnableRecordingAndPricesAreCurrency() {
        var recorded = 0
        var quantity = 0 to 0
        show(
            {
                SaleUi(
                    customer = ana,
                    store = store,
                    registration = ready,
                    sale = SaleBuilderState(lines = listOf(SaleLine(socks, 2)))
                )
            },
            SaleActions(onReview = { recorded++ }, onSetQuantity = { id, q -> quantity = id to q })
        )
        compose.onNodeWithText("Ana Torres").assertExists()
        compose.onNodeWithText("ana@example.com").assertExists()
        // The segment lives only in the insights card, never in the customer card.
        compose.onNodeWithText("Loyal regulars").assertDoesNotExist()
        compose.onNodeWithText("Change").assertExists()
        compose.onNodeWithText("$12.50 each").assertExists()
        compose.onNodeWithTag("sale-total").assertTextEquals("$25.00")
        // Each line's stepper names its product, so TalkBack says which line it changes.
        compose.onNodeWithContentDescription("Increase quantity of Trail socks").performScrollTo().performClick()
        assertEquals(1 to 3, quantity)
        compose.onNodeWithContentDescription("Remove Trail socks").performScrollTo().performClick()
        assertEquals(1 to 0, quantity)
        compose.onNodeWithTag("record-sale").assertIsEnabled().performClick()
        assertEquals(1, recorded)
    }

    @Test
    fun searchingCustomersUsesTheImeActionAndShowsAnEmptyState() {
        var searched: Pair<String, String>? = null
        show({ SaleUi(store = store) }, SaleActions(onSearchCustomers = { q, e -> searched = q to e }))
        compose.onNodeWithTag("customer-query").performTextInput("zzz")
        compose.onNodeWithTag("customer-query").performImeAction()
        assertEquals("zzz" to "", searched)
        compose.onNodeWithText("No customers found").assertExists()
    }

    @Test
    fun customerSearchShowsLoadingResultsAndErrors() {
        var ui by mutableStateOf(SaleUi(store = store, customers = CustomerSearchState(loading = true)))
        show({ ui })
        compose.onNodeWithText("Searching customers…").assertExists()
        ui = ui.copy(customers = CustomerSearchState(customers = listOf(ana)))
        compose.onNodeWithText("Ana Torres").assertExists()
        compose.onNodeWithText("ana@example.com").assertExists()
        ui = ui.copy(customers = CustomerSearchState(error = ApiError(503, "down", "Service down", null)))
        compose.onNodeWithText("Service down").assertExists()
        compose.onNodeWithText("Retry").assertExists()
    }

    @Test
    fun productSearchShowsLoadingEmptyAndPricedResults() {
        var added: Product? = null
        var ui by mutableStateOf(SaleUi(store = store, sale = SaleBuilderState(loading = true)))
        show({ ui }, SaleActions(onAddProduct = { added = it }))
        compose.onNodeWithText("Searching products…").assertExists()
        ui = ui.copy(sale = SaleBuilderState(products = listOf(socks)))
        compose.onNodeWithText("$12.50").performScrollTo().performClick()
        assertEquals(socks, added)
        ui = ui.copy(sale = SaleBuilderState(query = "zzz"))
        compose.onNodeWithTag("product-query").performTextInput("zzz")
        compose.onNodeWithTag("product-query").performImeAction()
        compose.onNodeWithText("No products found").assertExists()
    }

    @Test
    fun aQueuedSaleOffersAnotherAndHidesRecord() {
        var again = 0
        show(
            { SaleUi(customer = ana, store = store, registration = ready, sale = SaleBuilderState(queuedRowId = 7)) },
            SaleActions(onNewSale = { again++ })
        )
        compose.onNodeWithTag("sale-saved").assertExists()
        compose.onNodeWithTag("record-sale").assertDoesNotExist()
        compose.onNodeWithText("Start another sale").performClick()
        assertEquals(1, again)
    }

    @Test
    fun historyRowsCarryTextBadges() {
        val rows =
            listOf(
                row(1, RowState.PENDING),
                row(2, RowState.DONE),
                row(3, RowState.REFUSED, code = "future_dated")
            )
        show({ SaleUi(store = store, history = rows) })
        compose.onNodeWithText("Waiting to send").performScrollTo().assertExists()
        compose.onNodeWithText("Sent").performScrollTo().assertExists()
        compose.onNodeWithText("Needs attention").performScrollTo().assertExists()
    }

    @Test
    fun registrationOpensASheetAndClearsAfterSuccess() {
        var ui by mutableStateOf(SaleUi(store = store, registration = ready))
        show({ ui })
        compose.onNodeWithText("New customer").performScrollTo().performClick()
        compose.waitForIdle()
        compose.onNodeWithText("Register customer").assertExists().assertIsNotEnabled()
        compose.onNodeWithText("Name").performTextInput("Ben Ruiz")
        compose.onNodeWithText("Email").performTextInput("not-an-email")
        // Keep develop's behavior: the service owns email validation; the client only requires a name and channel.
        compose.onNodeWithText("Register customer").assertIsEnabled()
        compose.onNode(hasText("In store").and(hasRole(Role.RadioButton))).assertExists()
        compose.onNodeWithText("Online").assertExists()
        ui = ui.copy(registration = ready.copy(customer = ana), customer = ana)
        compose.waitForIdle()
        compose.onNodeWithText("Register customer").assertDoesNotExist()
        compose.onNodeWithText("New customer").assertDoesNotExist()
        // Opening the form again starts empty.
        compose.onNodeWithText("Change").performClick()
        compose.onNodeWithText("New customer").performScrollTo().performClick()
        compose.onNodeWithText("Ben Ruiz").assertDoesNotExist()
    }

    // ---- F27-05: promotions, the summary, the receipt and a refusal ----

    private val winter = Promotion(51, 12, "Winter 20", "20.00", "2026-11-01", "2026-11-30", listOf(1), listOf(1), true)
    private val discounts =
        DiscountState(
            storeId = 1,
            channelId = 1,
            promotions = listOf(winter),
            maxDiscountPercent = "15.00",
            loaded = true
        )
    private val shirt = Product(2, "SKU2", "Rain shell", 3, "40.00", true)

    private fun cart(vararg lines: SaleLine, reviewing: Boolean = false, queuedRowId: Long? = null) = SaleBuilderState(
        lines = lines.toList(),
        discounts = discounts,
        reviewing = reviewing,
        queuedRowId = queuedRowId
    )

    @Test
    fun aLineOffersTheListedPromotionWithinTheStoreCapAndTheBarShowsTheDiscount() {
        var chosen: Pair<Int, Int?>? = null
        show(
            { SaleUi(customer = ana, store = store, registration = ready, sale = cart(SaleLine(shirt, 1, winter))) },
            SaleActions(onChoosePromotion = { id, promotion -> chosen = id to promotion })
        )
        compose.onNodeWithText("Winter 20").performScrollTo().assertExists()
        compose.onNodeWithText("15% off, the most this store allows").assertExists()
        compose.onNodeWithText("$34.00 each, list price $40.00").assertExists()
        compose.onNodeWithTag("sale-subtotal").assertTextEquals("$40.00")
        compose.onNodeWithTag("sale-discount").assertTextEquals("−$6.00")
        compose.onNodeWithTag("sale-total").assertTextEquals("$34.00")
        compose.onNodeWithText("No promotion").performScrollTo().performClick()
        assertEquals(2 to null, chosen)
    }

    @Test
    fun theSummaryShowsTheLinesTheDiscountAndTheTotalAndOnlyItsButtonConfirms() {
        var confirmed = 0
        var back = 0
        show(
            {
                SaleUi(
                    customer = ana,
                    store = store,
                    registration = ready,
                    sale = cart(SaleLine(shirt, 2, winter), SaleLine(socks, 1), reviewing = true)
                )
            },
            SaleActions(onConfirm = { confirmed++ }, onCancelReview = { back++ })
        )
        compose.waitForIdle()
        compose.onNodeWithText("Check the sale").assertExists()
        compose.onNodeWithText("Customer: Ana Torres").assertExists()
        compose.onNodeWithText("2 × $34.00").assertExists()
        compose.onNodeWithText("1 × $12.50").assertExists()
        compose.onAllNodesWithTag("sale-discount").onFirst().assertTextEquals("−$12.00")
        compose.onAllNodesWithTag("sale-total").onFirst().assertTextEquals("$80.50")
        assertEquals(0, confirmed)
        compose.onNodeWithTag("confirm-sale").performScrollTo().performClick()
        assertEquals(1, confirmed)
        compose.onNodeWithText("Back to cart").performScrollTo().performClick()
        assertEquals(1, back)
    }

    @Test
    fun aRecordedSaleShowsItsReceiptWithTheSaleId() {
        val sent = """{"customer_id":"c1","store_id":1,"channel_id":1,"occurred_at":"2026-11-18T12:00:00Z",""" +
            """"lines":[{"product_id":2,"quantity":2,"unit_price":"34.00","promotion_id":51}]}"""
        val answer = """{"sale_id":4812,"customer_id":"c1","store_id":1,"channel_id":1,""" +
            """"occurred_at":"2026-11-18T18:00:00Z","total":"68.00",""" +
            """"lines":[{"product_id":2,"quantity":2,"unit_price":"34.00"}]}"""
        val done = row(7, RowState.DONE).copy(body = sent, status = 201, responseBody = answer)
        show(
            {
                SaleUi(
                    customer = ana,
                    store = store,
                    sale = cart(SaleLine(shirt, 2, winter), queuedRowId = 7),
                    queued = done
                )
            }
        )
        compose.onNodeWithTag("receipt-sale-id").assertTextEquals("Sale 4812")
        // Once in the receipt and once in the kept cart below it.
        compose.onAllNodesWithText("Rain shell").assertCountEquals(2)
        compose.onAllNodesWithText("Winter 20").assertCountEquals(2)
        compose.onNodeWithTag("receipt-discount").assertTextEquals("−$12.00")
        compose.onNodeWithTag("receipt-total").assertTextEquals("$68.00")
    }

    @Test
    fun aRefusedSaleSaysWhyNamesTheLineAndOffersTheKeptCart() {
        var back = 0
        val sent = """{"customer_id":"c1","store_id":1,"channel_id":1,"occurred_at":"2026-11-18T12:00:00Z",""" +
            """"lines":[{"product_id":2,"quantity":2,"unit_price":"30.00","promotion_id":51}]}"""
        val refused = row(7, RowState.REFUSED, code = "discount_above_promotion").copy(
            body = sent,
            status = 422,
            responseBody = """{"code":"discount_above_promotion","message":"x",""" +
                """"details":[{"field":"lines[0].unit_price","code":"below_promotion"}]}"""
        )
        show(
            {
                SaleUi(
                    customer = ana,
                    store = store,
                    registration = ready,
                    sale = cart(SaleLine(shirt, 2, winter), queuedRowId = 7),
                    queued = refused
                )
            },
            SaleActions(onBackToCart = { back++ })
        )
        compose.onNodeWithText("A discount is larger than its promotion allows.").assertExists()
        compose.onNodeWithText("Line: Rain shell").assertExists()
        compose.onNodeWithText("Your cart is kept as it was. Fix it and confirm again.").assertExists()
        compose.onNodeWithTag("cart-line-2").performScrollTo().assertExists()
        compose.onNodeWithTag("back-to-cart").performClick()
        assertEquals(1, back)
    }

    private fun row(id: Long, state: RowState, code: String? = null) = OutboxRow(
        id = id,
        operation = Operation.SALE,
        path = "/v1/sales",
        body = "{}",
        clientId = "client-$id",
        userId = "u",
        createdAt = 1_700_000_000_000L + id,
        state = state,
        code = code,
        message = code?.let { "Refused" }
    )
}
