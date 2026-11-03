package online.maxthecoder.mosaiq.floor.ui

// ui-string-ok-file: preview (sample copy for design review, never on a screen)

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.tooling.preview.PreviewParameter
import androidx.compose.ui.tooling.preview.PreviewParameterProvider
import java.time.Instant
import java.time.ZoneId
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.Channel
import online.maxthecoder.mosaiq.floor.net.Customer
import online.maxthecoder.mosaiq.floor.net.ErrorDetail
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.net.Store
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.ui.checkin.CheckInContent
import online.maxthecoder.mosaiq.floor.ui.checkin.CheckInStatus
import online.maxthecoder.mosaiq.floor.ui.checkin.StorePickerContent
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqSnackbarHost
import online.maxthecoder.mosaiq.floor.ui.outbox.OutboxContent
import online.maxthecoder.mosaiq.floor.ui.sale.RegistrationFields
import online.maxthecoder.mosaiq.floor.ui.sale.RegistrationForm
import online.maxthecoder.mosaiq.floor.ui.sale.SaleActions
import online.maxthecoder.mosaiq.floor.ui.sale.SaleUi
import online.maxthecoder.mosaiq.floor.ui.shell.FloorBottomBar
import online.maxthecoder.mosaiq.floor.ui.shell.FloorLocation
import online.maxthecoder.mosaiq.floor.ui.shell.FloorTopBar
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/*
 * Previews for every screen's stateless content, each state in light and dark. A state list is a
 * PreviewParameterProvider, so one light and one dark function cover all of its states; the file
 * sits beside the screens and holds only previews (UX-16).
 */

private const val WIDTH = 375
private const val HEIGHT = 700

@Composable
private fun Frame(dark: Boolean, scroll: Boolean = false, content: @Composable () -> Unit) =
    MosaiqTheme(darkTheme = dark) {
        Surface(color = MaterialTheme.colorScheme.background) {
            val modifier = if (scroll) Modifier.verticalScroll(rememberScrollState()) else Modifier.fillMaxSize()
            Column(
                modifier.padding(Spacing.Lg),
                verticalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(Spacing.Lg)
            ) {
                content()
            }
        }
    }

private val now: Instant = Instant.parse("2026-10-26T20:00:00Z")

// ---- Sign-in ---------------------------------------------------------------------------------

private class SignInSample(val notice: String?, val error: ApiError?, val loading: Boolean, val filled: Boolean)

private class SignInStates : PreviewParameterProvider<SignInSample> {
    override val values =
        sequenceOf(
            SignInSample(null, null, loading = false, filled = false),
            SignInSample("Your session ended. Sign in again.", null, loading = false, filled = false),
            SignInSample(null, ApiError(401, "invalid_credentials", "raw", "c-1"), loading = false, filled = true),
            SignInSample(null, ApiError.unreachable(null), loading = false, filled = true),
            SignInSample(null, null, loading = true, filled = true)
        )
}

@Composable
private fun SignInSheet(dark: Boolean, s: SignInSample) = Frame(dark, scroll = true) {
    SignInContent(
        email = if (s.filled) "ana@mosaiq-demo.com" else "",
        password = if (s.filled) "secret" else "",
        onEmail = {},
        onPassword = {},
        notice = s.notice,
        error = s.error,
        loading = s.loading,
        onSubmit = {}
    )
}

@Preview(name = "Sign-in states, light", widthDp = WIDTH, heightDp = HEIGHT, showBackground = true)
@Composable
private fun SignInStatesLight(@PreviewParameter(SignInStates::class) s: SignInSample) = SignInSheet(false, s)

@Preview(name = "Sign-in states, dark", widthDp = WIDTH, heightDp = HEIGHT, showBackground = true)
@Composable
private fun SignInStatesDark(@PreviewParameter(SignInStates::class) s: SignInSample) = SignInSheet(true, s)

@Preview(name = "Session failed, light", widthDp = WIDTH, heightDp = HEIGHT, showBackground = true)
@Composable
private fun SessionFailedLight() = Frame(false, scroll = true) {
    SessionFailedContent(ApiError.unreachable(null), {}) { MqButton("Sign out", {}) }
}

@Preview(name = "Session failed, dark", widthDp = WIDTH, heightDp = HEIGHT, showBackground = true)
@Composable
private fun SessionFailedDark() = Frame(true, scroll = true) {
    SessionFailedContent(ApiError.unreachable(null), {}) { MqButton("Sign out", {}) }
}

// ---- Check-in --------------------------------------------------------------------------------

private class CheckInSample(val status: CheckInStatus, val denied: Boolean = false, val note: String? = null)

private class CheckInStates : PreviewParameterProvider<CheckInSample> {
    override val values =
        sequenceOf(
            CheckInSample(CheckInStatus.NotCheckedIn),
            CheckInSample(CheckInStatus.Sending),
            CheckInSample(CheckInStatus.Waiting, denied = true, note = "Location is off for this app."),
            CheckInSample(CheckInStatus.Done("14:32")),
            CheckInSample(CheckInStatus.NeedsAttention(ApiError(422, "too_far", "Too far away.", "c-9")))
        )
}

@Composable
private fun CheckInSheet(dark: Boolean, s: CheckInSample) = Frame(dark) {
    CheckInContent("Centro", "Monterrey, NL", s.status, null, s.note, s.denied, {}, {}, {})
}

@Preview(name = "Check-in states, light", widthDp = WIDTH, heightDp = HEIGHT, showBackground = true)
@Composable
private fun CheckInStatesLight(@PreviewParameter(CheckInStates::class) s: CheckInSample) = CheckInSheet(false, s)

@Preview(name = "Check-in states, dark", widthDp = WIDTH, heightDp = HEIGHT, showBackground = true)
@Composable
private fun CheckInStatesDark(@PreviewParameter(CheckInStates::class) s: CheckInSample) = CheckInSheet(true, s)

// ---- Store picker ----------------------------------------------------------------------------

private val stores =
    listOf(
        Store(1, "Centro", "Monterrey", "NL", true),
        Store(2, "Valle", "Monterrey", "NL", true),
        Store(3, "Cumbres", "Monterrey", "NL", true)
    )

private class PickerStates : PreviewParameterProvider<StoresState> {
    override val values =
        sequenceOf(
            StoresState(city = "Monterrey", stores = stores, totalPages = 2),
            StoresState(loading = true),
            StoresState(city = "Atlantis"),
            StoresState(error = ApiError.unreachable(null))
        )
}

@Composable
private fun PickerSheet(dark: Boolean, state: StoresState) = Frame(dark) {
    StorePickerContent(state, state.city, {}, {}, {}, {}, {})
}

@Preview(name = "Store picker states, light", widthDp = WIDTH, heightDp = HEIGHT, showBackground = true)
@Composable
private fun PickerStatesLight(@PreviewParameter(PickerStates::class) s: StoresState) = PickerSheet(false, s)

@Preview(name = "Store picker states, dark", widthDp = WIDTH, heightDp = HEIGHT, showBackground = true)
@Composable
private fun PickerStatesDark(@PreviewParameter(PickerStates::class) s: StoresState) = PickerSheet(true, s)

// ---- Outbox ----------------------------------------------------------------------------------

private fun outboxRow(id: Long, operation: Operation, state: RowState, user: String = "A", attempts: Int = 0) =
    OutboxRow(
        id = id,
        operation = operation,
        path = "/v1/x",
        body = "{}",
        clientId = "c$id",
        userId = user,
        createdAt = now.toEpochMilli() - id * 3_600_000,
        state = state,
        attempts = attempts,
        code = if (state == RowState.REFUSED) "validation_failed" else null,
        message = if (state == RowState.REFUSED) "The sale has no items." else null
    )

private class OutboxStates : PreviewParameterProvider<List<OutboxRow>> {
    override val values =
        sequenceOf(
            emptyList(),
            listOf(
                outboxRow(1, Operation.CHECK_IN, RowState.PENDING, attempts = 2),
                outboxRow(2, Operation.SALE, RowState.REFUSED),
                outboxRow(3, Operation.QR_SCAN, RowState.DONE),
                outboxRow(4, Operation.RECO_CLICK, RowState.PENDING, user = "B")
            )
        )
}

@Composable
private fun OutboxSheet(dark: Boolean, rows: List<OutboxRow>) = Frame(dark) {
    OutboxContent(rows, "A", emptyMap(), {}, now, ZoneId.of("UTC"))
}

@Preview(name = "Outbox states, light", widthDp = WIDTH, heightDp = HEIGHT, showBackground = true)
@Composable
private fun OutboxStatesLight(@PreviewParameter(OutboxStates::class) rows: List<OutboxRow>) = OutboxSheet(false, rows)

@Preview(name = "Outbox states, dark", widthDp = WIDTH, heightDp = HEIGHT, showBackground = true)
@Composable
private fun OutboxStatesDark(@PreviewParameter(OutboxStates::class) rows: List<OutboxRow>) = OutboxSheet(true, rows)

// ---- Sale: registration sheet content and the empty sale -------------------------------------

private val channels = listOf(Channel(1, "physical_store"), Channel(2, "online"))

private class RegistrationSample(
    val fields: RegistrationFields,
    val state: RegistrationState
)

private class RegistrationStates : PreviewParameterProvider<RegistrationSample> {
    override val values =
        sequenceOf(
            RegistrationSample(RegistrationFields(), RegistrationState(channels = channels, selectedChannelId = 1)),
            RegistrationSample(
                RegistrationFields("Ben Ruiz", "not-an-email", "5551234"),
                RegistrationState(channels = channels, selectedChannelId = 2)
            ),
            RegistrationSample(RegistrationFields(), RegistrationState(loading = true)),
            RegistrationSample(RegistrationFields("Ben Ruiz", "ben@example.com", ""), RegistrationState()),
            RegistrationSample(
                RegistrationFields("Ben Ruiz", "ben@example.com", ""),
                RegistrationState(
                    channels = channels,
                    selectedChannelId = 1,
                    error = ApiError(
                        422,
                        "validation_failed",
                        "Check the form.",
                        "c-1",
                        listOf(ErrorDetail("email", "already_used"))
                    ),
                    timeoutAdvice = true
                )
            )
        )
}

@Composable
private fun RegistrationSheetContent(dark: Boolean, s: RegistrationSample) = Frame(dark) {
    RegistrationForm(s.fields, {}, s.state, {}, {}, {})
}

@Preview(name = "New customer sheet, light", widthDp = WIDTH, heightDp = 900, showBackground = true)
@Composable
private fun RegistrationLight(@PreviewParameter(RegistrationStates::class) s: RegistrationSample) =
    RegistrationSheetContent(false, s)

@Preview(name = "New customer sheet, dark", widthDp = WIDTH, heightDp = 900, showBackground = true)
@Composable
private fun RegistrationDark(@PreviewParameter(RegistrationStates::class) s: RegistrationSample) =
    RegistrationSheetContent(true, s)

private val ana = Customer("c1", "Ana Torres", "ana@example.com", null, 1, "2025-01-01")
private val socks = Product(1, "SKU1", "Trail socks", 3, "12.5", true)

private class SaleStates : PreviewParameterProvider<SaleUi> {
    override val values =
        sequenceOf(
            SaleUi(
                store = ChosenStore(1, "Centro"),
                registration = RegistrationState(channels = channels, selectedChannelId = 1)
            ),
            SaleUi(
                customer = ana,
                store = ChosenStore(1, "Centro"),
                registration = RegistrationState(channels = channels, selectedChannelId = 1),
                sale = SaleBuilderState(lines = listOf(SaleLine(socks, 2)))
            ),
            SaleUi()
        )
}

@Composable
private fun SaleSheet(dark: Boolean, ui: SaleUi) = MosaiqTheme(darkTheme = dark) {
    Surface(color = MaterialTheme.colorScheme.background) {
        Column(Modifier.fillMaxSize().padding(Spacing.Lg)) { SalesContent(ui, SaleActions(), SnackbarHostState()) {} }
    }
}

@Preview(name = "Sale states, light", widthDp = WIDTH, heightDp = 900, showBackground = true)
@Composable
private fun SaleStatesLight(@PreviewParameter(SaleStates::class) ui: SaleUi) = SaleSheet(false, ui)

@Preview(name = "Sale states, dark", widthDp = WIDTH, heightDp = 900, showBackground = true)
@Composable
private fun SaleStatesDark(@PreviewParameter(SaleStates::class) ui: SaleUi) = SaleSheet(true, ui)

// ---- Insights --------------------------------------------------------------------------------

private class InsightsStates : PreviewParameterProvider<InsightsState> {
    override val values =
        sequenceOf(
            InsightsState(customerId = "c1", loading = true),
            InsightsState(customerId = "c1", assignmentError = ApiError.unreachable(null)),
            InsightsState(
                customerId = "c1",
                assignment = online.maxthecoder.mosaiq.floor.net.CurrentAssignment("c1", "no_open_assignment"),
                recommendationsError = ApiError.unreachable(null)
            )
        )
}

@Composable
private fun InsightsSheet(dark: Boolean, state: InsightsState) = Frame(dark, scroll = true) {
    CustomerInsightsContent(state, onRetry = {}, onOpen = {})
}

@Preview(name = "Insights loading and errors, light", widthDp = WIDTH, heightDp = HEIGHT, showBackground = true)
@Composable
private fun InsightsStatesLight(@PreviewParameter(InsightsStates::class) s: InsightsState) = InsightsSheet(false, s)

@Preview(name = "Insights loading and errors, dark", widthDp = WIDTH, heightDp = HEIGHT, showBackground = true)
@Composable
private fun InsightsStatesDark(@PreviewParameter(InsightsStates::class) s: InsightsState) = InsightsSheet(true, s)

// ---- The shell's bars ------------------------------------------------------------------------

@Composable
private fun ShellBars(dark: Boolean, pending: Int, selected: FloorLocation.Root) = MosaiqTheme(darkTheme = dark) {
    Surface(color = MaterialTheme.colorScheme.background) {
        Column(Modifier.fillMaxSize()) {
            FloorTopBar(SessionState.SignedIn("Demo A", "STORE_MANAGER"), ChosenStore(1, "Centro"), {}, {})
            Column(Modifier.weight(1f)) {}
            FloorBottomBar(selected, pending) {}
        }
    }
}

@Preview(name = "Shell, light", widthDp = WIDTH, heightDp = HEIGHT, showBackground = true)
@Composable
private fun ShellLight() = ShellBars(false, pending = 3, selected = FloorLocation.Sale)

@Preview(name = "Shell, dark", widthDp = WIDTH, heightDp = HEIGHT, showBackground = true)
@Composable
private fun ShellDark() = ShellBars(true, pending = 3, selected = FloorLocation.Outbox)

// ---- Snackbar --------------------------------------------------------------------------------

@Composable
private fun SnackbarSheet(dark: Boolean) = Frame(dark) {
    val host = remember { SnackbarHostState() }
    LaunchedEffect(Unit) { host.showSnackbar("Sale saved on this phone.", actionLabel = "Dismiss") }
    MqSnackbarHost(host)
}

@Preview(name = "Snackbar, light", widthDp = WIDTH, heightDp = 160, showBackground = true)
@Composable
private fun SnackbarLight() = SnackbarSheet(false)

@Preview(name = "Snackbar, dark", widthDp = WIDTH, heightDp = 160, showBackground = true)
@Composable
private fun SnackbarDark() = SnackbarSheet(true)
