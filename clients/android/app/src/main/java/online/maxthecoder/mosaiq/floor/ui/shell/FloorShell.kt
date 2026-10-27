package online.maxthecoder.mosaiq.floor.ui.shell

import androidx.activity.compose.BackHandler
import androidx.annotation.StringRes
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.em
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.ui.CustomerInsightsViewModel
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import online.maxthecoder.mosaiq.floor.ui.SalesScreen
import online.maxthecoder.mosaiq.floor.ui.SalesViewModel
import online.maxthecoder.mosaiq.floor.ui.ScanScreen
import online.maxthecoder.mosaiq.floor.ui.ScanViewModel
import online.maxthecoder.mosaiq.floor.ui.SessionState
import online.maxthecoder.mosaiq.floor.ui.checkin.StoreAndCheckIn
import online.maxthecoder.mosaiq.floor.ui.outbox.OutboxList
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

private val WORDMARK_TRACKING = 0.14.em

/** The floor process once signed in: top bar, bottom navigation and the section the location names. */
@Composable
fun FloorHome(
    signedIn: SessionState.SignedIn,
    model: FloorViewModel,
    scan: ScanViewModel,
    sales: SalesViewModel,
    insights: CustomerInsightsViewModel,
    onSignOut: () -> Unit
) {
    var location: FloorLocation by rememberSaveable(stateSaver = FloorLocationSaver) {
        mutableStateOf(FloorLocation.CheckIn)
    }
    val chosen by model.chosen.collectAsStateWithLifecycle()
    val rows by model.open.collectAsStateWithLifecycle()
    val pendingWrites = rows.count { it.state == RowState.PENDING }

    // Back steps one state (ADR-0058); on Check-in it is off, so the system leaves the app.
    val back = location.back
    BackHandler(enabled = back != null) { back?.let { location = it } }

    SignOutHost(model, onSignOut) { requestSignOut ->
        Scaffold(
            contentWindowInsets = WindowInsets.safeDrawing,
            topBar = {
                FloorTopBar(
                    signedIn = signedIn,
                    store = chosen,
                    onStoreClick = { location = FloorLocation.StorePicker },
                    onSignOut = requestSignOut
                )
            },
            bottomBar = {
                FloorBottomBar(
                    selected = location.root,
                    pendingWrites = pendingWrites,
                    onSelect = { location = it }
                )
            }
        ) { innerPadding ->
            // Each section scrolls inside the room left between the bars.
            Box(modifier = Modifier.fillMaxSize().padding(innerPadding).padding(Spacing.Lg)) {
                when (val here = location) {
                    FloorLocation.CheckIn, FloorLocation.StorePicker ->
                        StoreAndCheckIn(
                            model,
                            picking = here == FloorLocation.StorePicker,
                            onChangeStore = { location = FloorLocation.StorePicker },
                            onPicked = { location = FloorLocation.CheckIn }
                        )
                    FloorLocation.Scan, is FloorLocation.ProductFromRecommendation ->
                        ScanScreen(
                            scan,
                            chosen,
                            onPickStore = { location = FloorLocation.CheckIn },
                            onAddProduct = sales::addProduct
                        )
                    FloorLocation.Sale ->
                        SalesScreen(
                            sales,
                            chosen,
                            onPickStore = { location = FloorLocation.CheckIn },
                            insights = insights,
                            onOpenProduct = { productId ->
                                scan.showProduct(productId)
                                location = FloorLocation.ProductFromRecommendation(productId)
                            }
                        )
                    FloorLocation.Outbox -> OutboxList(model)
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun FloorTopBar(
    signedIn: SessionState.SignedIn,
    store: ChosenStore?,
    onStoreClick: () -> Unit,
    onSignOut: () -> Unit
) {
    TopAppBar(
        title = {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(Spacing.Sm)
            ) {
                // At large font sizes the wordmark gives its room to the store name.
                if (LocalDensity.current.fontScale <= LARGE_FONT_SCALE) {
                    Text(
                        stringResource(R.string.signin_wordmark),
                        style = MaterialTheme.typography.titleMedium.copy(
                            fontWeight = FontWeight.Bold,
                            letterSpacing = WORDMARK_TRACKING
                        ),
                        maxLines = 1,
                        modifier = Modifier.semantics { heading() }
                    )
                }
                AssistChip(
                    onClick = onStoreClick,
                    modifier = Modifier.testTag("store-chip"),
                    label = {
                        Text(
                            store?.name ?: stringResource(R.string.shell_pick_store),
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                    },
                    leadingIcon = {
                        Icon(painterResource(R.drawable.ic_lucide_store), contentDescription = null)
                    }
                )
            }
        },
        actions = { AccountMenu(signedIn, onSignOut) }
    )
}

@Composable
private fun AccountMenu(signedIn: SessionState.SignedIn, onSignOut: () -> Unit) {
    var open by rememberSaveable { mutableStateOf(false) }
    Box {
        IconButton(onClick = { open = true }, modifier = Modifier.testTag("account-menu")) {
            Icon(
                painterResource(R.drawable.ic_lucide_circle_user),
                contentDescription = stringResource(R.string.shell_account_menu)
            )
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            Column(modifier = Modifier.padding(horizontal = Spacing.Lg, vertical = Spacing.Sm)) {
                Text(signedIn.name, style = MaterialTheme.typography.titleSmall)
                Text(signedIn.role, style = MaterialTheme.typography.bodySmall)
            }
            HorizontalDivider()
            DropdownMenuItem(
                text = { Text(stringResource(R.string.session_sign_out)) },
                leadingIcon = {
                    Icon(painterResource(R.drawable.ic_lucide_log_out), contentDescription = null)
                },
                onClick = {
                    open = false
                    onSignOut()
                }
            )
        }
    }
}

private const val LARGE_FONT_SCALE = 1.3f

private class Section(val root: FloorLocation.Root, @StringRes val label: Int, val icon: Int, val tag: String)

private val SECTIONS =
    listOf(
        Section(FloorLocation.CheckIn, R.string.nav_check_in, R.drawable.ic_lucide_map_pin, "nav-check-in"),
        Section(FloorLocation.Scan, R.string.nav_scan, R.drawable.ic_lucide_scan_line, "nav-scan"),
        Section(FloorLocation.Sale, R.string.nav_sale, R.drawable.ic_lucide_shopping_cart, "nav-sale"),
        Section(FloorLocation.Outbox, R.string.nav_outbox, R.drawable.ic_lucide_inbox, "nav-outbox")
    )

@Composable
internal fun FloorBottomBar(selected: FloorLocation.Root, pendingWrites: Int, onSelect: (FloorLocation) -> Unit) {
    val largeFont = LocalDensity.current.fontScale > LARGE_FONT_SCALE
    NavigationBar {
        SECTIONS.forEach { section ->
            NavigationBarItem(
                selected = section.root == selected,
                onClick = { onSelect(section.root) },
                modifier = Modifier.testTag(section.tag),
                icon = {
                    BadgedBox(
                        badge = {
                            if (section.root == FloorLocation.Outbox && pendingWrites > 0) {
                                val waiting =
                                    pluralStringResource(R.plurals.a11y_outbox_waiting, pendingWrites, pendingWrites)
                                Badge(
                                    modifier = Modifier.testTag("outbox-badge")
                                        .semantics { contentDescription = waiting }
                                ) { Text(pendingWrites.toString()) }
                            }
                        }
                    ) {
                        Icon(
                            painterResource(section.icon),
                            contentDescription = null
                        )
                    }
                },
                // At large font sizes a label wraps mid-word, so only the selected tab draws one; the others
                // keep it in the semantics tree for TalkBack.
                alwaysShowLabel = !largeFont,
                label = { Text(stringResource(section.label)) }
            )
        }
    }
}
