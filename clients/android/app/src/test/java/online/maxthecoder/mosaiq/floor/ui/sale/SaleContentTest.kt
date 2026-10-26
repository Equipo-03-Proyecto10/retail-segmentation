package online.maxthecoder.mosaiq.floor.ui.sale

import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertTextEquals
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
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
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.ui.CustomerSearchState
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
                    segmentLabel = "Loyal regulars",
                    sale = SaleBuilderState(lines = listOf(SaleLine(socks, 2)))
                )
            },
            SaleActions(onConfirm = { recorded++ }, onSetQuantity = { id, q -> quantity = id to q })
        )
        compose.onNodeWithText("Ana Torres").assertExists()
        compose.onNodeWithText("ana@example.com").assertExists()
        compose.onNodeWithText("Loyal regulars").assertExists()
        compose.onNodeWithText("Change").assertExists()
        compose.onNodeWithText("$12.50 each").assertExists()
        compose.onNodeWithTag("sale-total").assertTextEquals("$25.00")
        compose.onNodeWithContentDescription("Increase quantity").performScrollTo().performClick()
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
        var registered: Triple<String, String, String>? = null
        show({ ui }, SaleActions(onRegister = { n, e, p -> registered = Triple(n, e, p) }))
        compose.onNodeWithText("New customer").performScrollTo().performClick()
        compose.waitForIdle()
        compose.onNodeWithText("Register customer").assertExists().assertIsNotEnabled()
        compose.onNodeWithText("Name").performTextInput("Ben Ruiz")
        compose.onNodeWithText("Email").performTextInput("not-an-email")
        compose.onNodeWithText("Enter a valid email address").assertExists()
        compose.onNodeWithText("Register customer").assertIsNotEnabled()
        compose.onNode(hasText("In store").and(hasRole(Role.RadioButton))).assertExists()
        compose.onNodeWithText("Online").assertExists()
        ui = ui.copy(registration = ready.copy(customer = ana), customer = ana)
        compose.waitForIdle()
        compose.onNodeWithText("Register customer").assertDoesNotExist()
        compose.onNodeWithText("New customer").assertDoesNotExist()
        assertEquals(null, registered)
        // Opening the form again starts empty.
        compose.onNodeWithText("Change").performClick()
        compose.onNodeWithText("New customer").performScrollTo().performClick()
        compose.onNodeWithText("Ben Ruiz").assertDoesNotExist()
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
