package online.maxthecoder.mosaiq.floor.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.Dp
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqErrorPanel
import online.maxthecoder.mosaiq.floor.ui.components.MqIcon
import online.maxthecoder.mosaiq.floor.ui.components.MqLoading
import online.maxthecoder.mosaiq.floor.ui.components.MqTextField
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/** The widest the pre-sign-in column grows on a tablet or in landscape (480dp). */
private val MaxFormWidth: Dp = Spacing.X6l * 7.5f

@Composable
fun FloorApp(
    state: SessionState,
    onSignIn: (String, String) -> Unit,
    onRetry: () -> Unit,
    onSignOut: () -> Unit,
    floor: (@Composable (SessionState.SignedIn) -> Unit)? = null,
    signOut: (@Composable () -> Unit)? = null
) {
    // Kept here, above the screens, so a refused sign-in does not clear what was typed.
    var email by rememberSaveable { mutableStateOf("") }
    // A password is never saved across process death; it only outlives the call in flight.
    var password by remember { mutableStateOf("") }
    // True from the tap until the session answers, so the form stays up with a busy button.
    var submitted by rememberSaveable { mutableStateOf(false) }
    val signedIn = state is SessionState.SignedIn
    LaunchedEffect(signedIn) {
        // The next user to sign in starts from an empty form.
        if (signedIn) {
            email = ""
            password = ""
        }
    }
    LaunchedEffect(state) { if (state !is SessionState.Loading) submitted = false }
    MosaiqTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            // The floor shell has its own bars and insets, so it takes the whole window.
            if (state is SessionState.SignedIn && floor != null) {
                floor(state)
                return@Surface
            }
            PreSignInFrame {
                when (state) {
                    is SessionState.SignIn ->
                        SignInContent(
                            email = email,
                            password = password,
                            onEmail = { email = it },
                            onPassword = { password = it },
                            notice = state.notice,
                            error = state.error,
                            loading = false,
                            onSubmit = {
                                submitted = true
                                onSignIn(email, password)
                            }
                        )
                    SessionState.Loading ->
                        if (submitted) {
                            SignInContent(
                                email = email,
                                password = password,
                                onEmail = { email = it },
                                onPassword = { password = it },
                                notice = null,
                                error = null,
                                loading = true,
                                onSubmit = {}
                            )
                        } else {
                            MqLoading(stringResource(R.string.signin_loading_account), fillArea = true)
                        }
                    is SessionState.Failed ->
                        SessionFailedContent(
                            error = state.error,
                            onRetry = onRetry,
                            signOut = signOut ?: { MqButton(stringResource(R.string.session_sign_out), onSignOut) }
                        )
                    is SessionState.SignedIn -> SignedInPanel(state, onSignOut)
                }
            }
        }
    }
}

/** Bars, cutout and keyboard are kept clear of; the column is centered, capped in width, and scrolls. */
@Composable
private fun PreSignInFrame(content: @Composable () -> Unit) {
    Box(modifier = Modifier.fillMaxSize().safeDrawingPadding(), contentAlignment = Alignment.Center) {
        Column(
            modifier =
            Modifier
                .widthIn(max = MaxFormWidth)
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(Spacing.Lg),
            verticalArrangement = Arrangement.spacedBy(Spacing.Lg)
        ) { content() }
    }
}

@Composable
private fun Wordmark() {
    Column(verticalArrangement = Arrangement.spacedBy(Spacing.Xs)) {
        Text(
            stringResource(R.string.signin_wordmark),
            style = MaterialTheme.typography.headlineMedium,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.semantics { heading() }
        )
        Text(
            stringResource(R.string.signin_help),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}

/** The sign-in form: stateless, so previews and tests can drive every state. */
@Composable
fun SignInContent(
    email: String,
    password: String,
    onEmail: (String) -> Unit,
    onPassword: (String) -> Unit,
    notice: String?,
    error: ApiError?,
    loading: Boolean,
    onSubmit: () -> Unit
) {
    val ready = email.isNotBlank() && password.isNotEmpty() && !loading
    Wordmark()
    notice?.let {
        Row(horizontalArrangement = Arrangement.spacedBy(Spacing.Sm), verticalAlignment = Alignment.Top) {
            MqIcon(R.drawable.ic_lucide_info, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
            Text(
                it,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.semantics {
                    liveRegion = LiveRegionMode.Polite
                }
            )
        }
    }
    MqTextField(
        value = email,
        onValueChange = onEmail,
        label = stringResource(R.string.signin_email),
        enabled = !loading,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email, imeAction = ImeAction.Next)
    )
    MqTextField(
        value = password,
        onValueChange = onPassword,
        label = stringResource(R.string.signin_password),
        enabled = !loading,
        password = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
        keyboardActions = KeyboardActions(onDone = { if (ready) onSubmit() })
    )
    MqButton(
        stringResource(R.string.signin_submit),
        onSubmit,
        fullWidth = true,
        enabled = ready || loading,
        loading = loading
    )
    error?.let { MqErrorPanel(it, onRetry = if (ready) onSubmit else null, friendly = friendlySignInMessage(it)) }
}

/** Plain words for the failures a person can act on; null leaves the service's own message. */
@Composable
internal fun friendlySignInMessage(error: ApiError): String? = when {
    error.code == "unreachable" -> stringResource(R.string.signin_error_unreachable)
    error.code == "invalid_credentials" || error.status == 401 -> stringResource(R.string.signin_error_credentials)
    error.status >= SERVER_ERROR -> stringResource(R.string.signin_error_server)
    else -> null
}

private const val SERVER_ERROR = 500

/** Signed in, but the profile could not be read: retry, or sign out. */
@Composable
fun SessionFailedContent(error: ApiError, onRetry: () -> Unit, signOut: @Composable () -> Unit) {
    Wordmark()
    Text(
        stringResource(R.string.session_failed_title),
        style = MaterialTheme.typography.titleMedium,
        modifier = Modifier.semantics { heading() }
    )
    MqErrorPanel(error, onRetry = onRetry, friendly = friendlySignInMessage(error))
    Box(Modifier.fillMaxWidth()) {
        // The floor's sign-out warns about queued writes first.
        signOut()
    }
}

@Composable
private fun SignedInPanel(state: SessionState.SignedIn, onSignOut: () -> Unit) {
    Text(stringResource(R.string.session_signed_in_as), style = MaterialTheme.typography.labelLarge)
    Text(state.name, style = MaterialTheme.typography.titleLarge)
    Text(state.role, style = MaterialTheme.typography.bodyLarge)
    MqButton(stringResource(R.string.session_sign_out), onSignOut, variant = MqButtonVariant.Secondary)
}

/** A refusal in plain words; the code and correlation id sit behind "Details" (see [MqErrorPanel]). */
@Composable
fun ErrorPanel(error: ApiError) {
    MqErrorPanel(error)
}

private const val PREVIEW_WIDTH = 375
private const val PREVIEW_HEIGHT = 700

@Composable
private fun SignInPreview(dark: Boolean, error: ApiError?, loading: Boolean) = MosaiqTheme(darkTheme = dark) {
    Surface {
        PreSignInFrame {
            SignInContent("ana@mosaiq-demo.com", "secret", {}, {}, null, error, loading, {})
        }
    }
}

@Preview(name = "Sign-in, light", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun SignInLight() = SignInPreview(false, null, false)

@Preview(name = "Sign-in error, dark", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun SignInErrorDark() = SignInPreview(true, ApiError.unreachable(null), false)

@Preview(name = "Sign-in busy, light", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun SignInBusyLight() = SignInPreview(false, null, true)
