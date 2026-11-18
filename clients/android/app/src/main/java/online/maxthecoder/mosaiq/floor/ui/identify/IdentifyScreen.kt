package online.maxthecoder.mosaiq.floor.ui.identify

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.ui.CameraPermission
import online.maxthecoder.mosaiq.floor.ui.CardRead
import online.maxthecoder.mosaiq.floor.ui.FLOOR_SEARCH_MIN
import online.maxthecoder.mosaiq.floor.ui.FloorProfileState
import online.maxthecoder.mosaiq.floor.ui.FloorSearchState
import online.maxthecoder.mosaiq.floor.ui.IdentifyViewModel
import online.maxthecoder.mosaiq.floor.ui.components.MqBadge
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqEmptyState
import online.maxthecoder.mosaiq.floor.ui.components.MqErrorPanel
import online.maxthecoder.mosaiq.floor.ui.components.MqListRow
import online.maxthecoder.mosaiq.floor.ui.components.MqLoading
import online.maxthecoder.mosaiq.floor.ui.components.MqPager
import online.maxthecoder.mosaiq.floor.ui.components.MqSection
import online.maxthecoder.mosaiq.floor.ui.components.MqTextField
import online.maxthecoder.mosaiq.floor.ui.components.MqTone
import online.maxthecoder.mosaiq.floor.ui.scan.CameraPreview
import online.maxthecoder.mosaiq.floor.ui.scan.CameraViewport
import online.maxthecoder.mosaiq.floor.ui.scan.ViewportStart
import online.maxthecoder.mosaiq.floor.ui.scan.openAppSettings
import online.maxthecoder.mosaiq.floor.ui.scan.reconcilePermission
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/**
 * The Customer section (F27-02): read the customer's loyalty QR, or search by name, and open
 * their profile in this role's floor view. The camera is asked for only at the first Scan and
 * checked again on every resume; without it, the search still works. [profileId] is the
 * customer the location names, so a restored location reads the profile again.
 */
@Composable
fun IdentifyScreen(model: IdentifyViewModel, profileId: String?, onOpenProfile: (String) -> Unit, onBack: () -> Unit) {
    if (profileId != null) {
        LaunchedEffect(profileId) { model.open(profileId) }
        val profile by model.profile.collectAsStateWithLifecycle()
        ProfileBody(profile, onRetry = model::retryProfile, onBack = onBack)
        return
    }
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val granted = {
        ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
    }
    var permission by rememberSaveable {
        mutableStateOf(if (granted()) CameraPermission.GRANTED else CameraPermission.NOT_ASKED)
    }
    var scanning by rememberSaveable { mutableStateOf(false) }
    var cameraError by remember { mutableStateOf(false) }
    val launcher =
        rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
            permission = if (ok) CameraPermission.GRANTED else CameraPermission.DENIED
            scanning = ok
        }
    val currentPermission by rememberUpdatedState(permission)
    DisposableEffect(owner, context) {
        val observer =
            LifecycleEventObserver { _, event ->
                if (event == Lifecycle.Event.ON_RESUME) {
                    val after = reconcilePermission(currentPermission, granted())
                    if (after != currentPermission) {
                        permission = after
                        scanning = false
                    }
                }
            }
        owner.lifecycle.addObserver(observer)
        onDispose { owner.lifecycle.removeObserver(observer) }
    }
    val card by model.card.collectAsStateWithLifecycle()
    val search by model.search.collectAsStateWithLifecycle()
    // Any answer ends the read: the camera is off until "Scan again".
    LaunchedEffect(card) { if (card != CardRead.Idle) scanning = false }
    val scan = {
        cameraError = false
        model.scanAgain()
        if (permission == CameraPermission.GRANTED) scanning = true else launcher.launch(Manifest.permission.CAMERA)
    }
    IdentifyBody(
        permission = permission,
        scanning = scanning,
        cameraError = cameraError,
        card = card,
        search = search,
        onScan = scan,
        onGrantAccess = { launcher.launch(Manifest.permission.CAMERA) },
        onOpenSettings = { openAppSettings(context) },
        onSearch = { query, page -> model.search(query, page) },
        onOpenProfile = onOpenProfile,
        camera = {
            CameraPreview(onDecoded = { model.onDecoded(it, onOpenProfile) }, onError = { cameraError = true })
        }
    )
}

/** Every state of the identification screen from plain values, for previews and tests. */
@Composable
fun IdentifyBody(
    permission: CameraPermission,
    scanning: Boolean,
    cameraError: Boolean,
    card: CardRead,
    search: FloorSearchState,
    onScan: () -> Unit,
    onGrantAccess: () -> Unit,
    onOpenSettings: () -> Unit,
    onSearch: (String, Int) -> Unit,
    onOpenProfile: (String) -> Unit,
    camera: @Composable () -> Unit
) {
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(Spacing.Lg)
    ) {
        Text(
            stringResource(R.string.identify_title),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.semantics { heading() }
        )
        when {
            permission == CameraPermission.DENIED -> {
                MqEmptyState(
                    icon = R.drawable.ic_lucide_triangle_alert,
                    title = stringResource(R.string.scan_denied_title),
                    message = stringResource(R.string.identify_denied_message),
                    actionLabel = stringResource(R.string.scan_grant_access),
                    onAction = onGrantAccess,
                    modifier = Modifier.testTag("camera-denied")
                )
                MqButton(stringResource(R.string.scan_open_settings), onOpenSettings, variant = MqButtonVariant.Quiet)
            }
            card != CardRead.Idle -> CardAnswer(card, onScan)
            cameraError && scanning ->
                MqEmptyState(
                    icon = R.drawable.ic_lucide_circle_alert,
                    title = stringResource(R.string.scan_camera_error_title),
                    message = stringResource(R.string.identify_camera_error_message),
                    actionLabel = stringResource(R.string.scan_retry),
                    onAction = onScan
                )
            scanning ->
                CameraViewport(
                    camera,
                    description = stringResource(R.string.identify_camera_description),
                    hint = stringResource(R.string.identify_scan_hint)
                )
            else -> ViewportStart(onScan, help = stringResource(R.string.identify_scan_help))
        }
        FloorSearch(search, onSearch, onOpenProfile)
    }
}

/** A read that opened nothing. Unknown and revoked say so and nothing more: no code, no customer. */
@Composable
private fun CardAnswer(card: CardRead, onScanAgain: () -> Unit) {
    val message: @Composable (Int, Int, String) -> Unit = { title, text, tag ->
        MqEmptyState(
            icon = R.drawable.ic_lucide_circle_alert,
            title = stringResource(title),
            message = stringResource(text),
            actionLabel = stringResource(R.string.identify_scan_again),
            onAction = onScanAgain,
            modifier = Modifier.testTag(tag)
        )
    }
    when (card) {
        CardRead.Idle -> Unit
        CardRead.Looking -> MqLoading(stringResource(R.string.identify_looking))
        CardRead.NotACard ->
            message(R.string.identify_not_a_card_title, R.string.identify_not_a_card_message, "card-not-a-card")
        CardRead.Unknown -> message(R.string.identify_unknown_title, R.string.identify_unknown_message, "card-unknown")
        CardRead.Revoked -> message(R.string.identify_revoked_title, R.string.identify_revoked_message, "card-revoked")
        is CardRead.Failed -> MqErrorPanel(card.error, onRetry = onScanAgain)
    }
}

@Composable
private fun FloorSearch(state: FloorSearchState, onSearch: (String, Int) -> Unit, onOpenProfile: (String) -> Unit) {
    var query by rememberSaveable { mutableStateOf(state.query) }
    val ready = query.trim().length >= FLOOR_SEARCH_MIN
    val submit = { if (ready) onSearch(query, 1) }
    MqSection(stringResource(R.string.identify_search_title)) {
        MqTextField(
            query,
            { query = it },
            stringResource(R.string.identify_search_label),
            supportingText = stringResource(R.string.identify_search_help),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = { submit() }),
            modifier = Modifier.testTag("floor-search-query")
        )
        MqButton(
            stringResource(R.string.sale_search_button),
            { submit() },
            enabled = ready && !state.loading,
            fullWidth = true,
            variant = MqButtonVariant.Secondary,
            modifier = Modifier.testTag("floor-search-button")
        )
        when {
            state.loading -> MqLoading(stringResource(R.string.sale_search_loading))
            state.error != null -> MqErrorPanel(state.error, onRetry = { onSearch(state.query, state.page) })
            state.searched && state.customers.isEmpty() ->
                MqEmptyState(
                    icon = R.drawable.ic_lucide_user_search,
                    title = stringResource(R.string.sale_search_empty_title),
                    message = stringResource(R.string.identify_search_empty_help),
                    modifier = Modifier.testTag("floor-search-empty")
                )
        }
        if (!state.loading) {
            state.customers.forEach { customer ->
                MqListRow(
                    headline = customer.name,
                    supporting = customer.email ?: customer.phone,
                    onClick = { onOpenProfile(customer.customerId) },
                    chevron = true,
                    divider = true
                )
            }
            if (state.totalPages > 1) {
                MqPager(
                    state.page,
                    state.totalPages,
                    onPrevious = { onSearch(state.query, state.page - 1) },
                    onNext = { onSearch(state.query, state.page + 1) }
                )
            }
        }
    }
}

/** The customer in this role's floor view: exactly the elements the service returned. */
@Composable
fun ProfileBody(state: FloorProfileState, onRetry: () -> Unit, onBack: () -> Unit) {
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(Spacing.Lg)
    ) {
        val customer = state.customer
        when {
            state.error != null -> MqErrorPanel(state.error, onRetry = onRetry)
            customer == null -> MqLoading(stringResource(R.string.identify_profile_loading))
            else ->
                MqSection(customer.name, Modifier.testTag("floor-profile")) {
                    if (state.byCard) {
                        MqBadge(
                            stringResource(R.string.identify_by_card),
                            tone = MqTone.Success,
                            leadingIcon = R.drawable.ic_lucide_circle_check
                        )
                    }
                    customer.email?.let { MqListRow(stringResource(R.string.identify_email), supporting = it) }
                    customer.phone?.let { MqListRow(stringResource(R.string.identify_phone), supporting = it) }
                }
        }
        MqButton(stringResource(R.string.identify_another), onBack, variant = MqButtonVariant.Quiet)
    }
}
