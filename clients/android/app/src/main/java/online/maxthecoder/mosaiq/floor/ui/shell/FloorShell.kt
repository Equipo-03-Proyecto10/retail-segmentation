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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.em
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.auth.FloorAccess
import online.maxthecoder.mosaiq.floor.auth.FloorSection
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
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.outbox.OutboxList
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing
import online.maxthecoder.mosaiq.floor.ui.theme.isLargeFont

private val WORDMARK_TRACKING = 0.14.em

/**
 * The floor process once signed in: top bar, bottom navigation and the section the location names.
 * Each role gets its own profile of sections (ADR-0060, [FloorAccess]); a role that has none is shown a
 * plain message instead of any screen.
 */
@Composable
fun FloorHome(
    signedIn: SessionState.SignedIn,
    model: FloorViewModel,
    scan: ScanViewModel,
    sales: SalesViewModel,
    insights: CustomerInsightsViewModel,
    onSignOut: () -> Unit
) {
    if (FloorAccess.sections(signedIn.role).isEmpty()) {
        TurnedAway(model::forgetSession, onSignOut)
    } else {
        FloorProfile(signedIn, model, scan, sales, insights, onSignOut)
    }
}

/** A role the app holds nothing for: no section, no bar, and a way back to sign-in. */
@Composable
private fun TurnedAway(forgetSession: () -> Unit, onSignOut: () -> Unit) {
    // The message says the user was signed out, so it is true by the time it shows: nothing queued goes
    // out under this session from here on.
    LaunchedEffect(Unit) { forgetSession() }
    Column(
        modifier = Modifier.fillMaxSize().padding(Spacing.Lg).testTag("turned-away"),
        verticalArrangement = Arrangement.spacedBy(Spacing.Md, Alignment.CenterVertically)
    ) {
        Text(stringResource(R.string.session_role_refused), style = MaterialTheme.typography.bodyLarge)
        MqButton(stringResource(R.string.session_sign_out), onClick = onSignOut)
    }
}

@Composable
private fun FloorProfile(
    signedIn: SessionState.SignedIn,
    model: FloorViewModel,
    scan: ScanViewModel,
    sales: SalesViewModel,
    insights: CustomerInsightsViewModel,
    onSignOut: () -> Unit
) {
    val role = signedIn.role
    var saved: FloorLocation by rememberSaveable(stateSaver = FloorLocationSaver) {
        mutableStateOf(FloorLocation.CheckIn.allowedFor(role))
    }
    // What was saved is only a request: a role never reaches a section it does not hold, whether the
    // location came from a tap, Back or a restored state (ADR-0058).
    val location = saved.allowedFor(role)
    LaunchedEffect(location) { if (location != saved) saved = location }
    val go: (FloorLocation) -> Unit = { saved = it.allowedFor(role) }
    val chosen by model.chosen.collectAsStateWithLifecycle()
    val rows by model.open.collectAsStateWithLifecycle()
    val pendingWrites = rows.orEmpty().count { it.state == RowState.PENDING }
    val canSell = FloorAccess.holds(role, FloorSection.SALE)

    // Back steps one state (ADR-0058); on the first section it is off, so the system leaves the app.
    val back = location.backFor(role)
    BackHandler(enabled = back != null) { back?.let(go) }

    SignOutHost(model, onSignOut) { requestSignOut ->
        Scaffold(
            contentWindowInsets = WindowInsets.safeDrawing,
            topBar = {
                FloorTopBar(
                    signedIn = signedIn,
                    store = chosen,
                    onStoreClick = { go(FloorLocation.StorePicker) },
                    onSignOut = requestSignOut
                )
            },
            bottomBar = {
                FloorBottomBar(
                    selected = location.root,
                    pendingWrites = pendingWrites,
                    held = FloorAccess.sections(role),
                    onSelect = go
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
                            onChangeStore = { go(FloorLocation.StorePicker) },
                            onPicked = { go(FloorLocation.CheckIn) }
                        )
                    FloorLocation.Scan, is FloorLocation.ProductFromRecommendation ->
                        ScanScreen(
                            scan,
                            chosen,
                            onPickStore = { go(FloorLocation.CheckIn) },
                            onAddProduct = if (canSell) sales::addProduct else null
                        )
                    FloorLocation.Sale, FloorLocation.RegisterCustomer ->
                        SalesScreen(
                            sales,
                            chosen,
                            onPickStore = { go(FloorLocation.CheckIn) },
                            insights = insights,
                            registering = here == FloorLocation.RegisterCustomer,
                            onRegisteringChange = { open ->
                                go(if (open) FloorLocation.RegisterCustomer else FloorLocation.Sale)
                            },
                            onOpenProduct = { productId ->
                                scan.showProduct(productId)
                                go(FloorLocation.ProductFromRecommendation(productId))
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
                if (!isLargeFont()) {
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
                // Material's menu item is clickable with no role, so TalkBack would not say "button".
                modifier = Modifier.semantics { role = Role.Button },
                onClick = {
                    open = false
                    onSignOut()
                }
            )
        }
    }
}

private class Section(
    val section: FloorSection,
    @StringRes val label: Int,
    val icon: Int,
    val tag: String
) {
    val root: FloorLocation.Root get() = section.root
}

private val SECTIONS =
    listOf(
        Section(FloorSection.CHECK_IN, R.string.nav_check_in, R.drawable.ic_lucide_map_pin, "nav-check-in"),
        Section(FloorSection.SCAN, R.string.nav_scan, R.drawable.ic_lucide_scan_line, "nav-scan"),
        Section(FloorSection.SALE, R.string.nav_sale, R.drawable.ic_lucide_shopping_cart, "nav-sale"),
        Section(FloorSection.OUTBOX, R.string.nav_outbox, R.drawable.ic_lucide_inbox, "nav-outbox")
    )

@Composable
internal fun FloorBottomBar(
    selected: FloorLocation.Root,
    pendingWrites: Int,
    held: List<FloorSection> = FloorSection.entries,
    onSelect: (FloorLocation) -> Unit
) {
    val largeFont = isLargeFont()
    NavigationBar {
        SECTIONS.filter { it.section in held }.forEach { section ->
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
