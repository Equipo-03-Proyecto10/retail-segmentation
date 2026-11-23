package online.maxthecoder.mosaiq.floor.ui.shell

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqDialog

/**
 * Signing out with writes still queued warns first: they wait for this user.
 * [content] draws whatever starts the sign-out and calls the function it is given;
 * the warning dialog is drawn here.
 */
@Composable
fun SignOutHost(
    model: FloorViewModel,
    onSignOut: () -> Unit,
    content: @Composable (requestSignOut: () -> Unit) -> Unit
) {
    val scope = rememberCoroutineScope()
    var pending by rememberSaveable { mutableStateOf(0) }
    content {
        scope.launch {
            val count = model.pendingForCurrentUser()
            // Room answers on its own thread; the dialog belongs to the main one.
            withContext(Dispatchers.Main.immediate) {
                pending = count
                if (count == 0) onSignOut()
            }
        }
    }
    if (pending > 0) {
        MqDialog(
            title = stringResource(R.string.signout_title),
            onDismiss = { pending = 0 },
            actions = {
                // Staying is the safe choice; signing out leaves the writes waiting, so it reads as danger.
                MqButton(stringResource(R.string.signout_stay), { pending = 0 }, variant = MqButtonVariant.Secondary)
                MqButton(
                    stringResource(R.string.signout_confirm),
                    {
                        pending = 0
                        onSignOut()
                    },
                    variant = MqButtonVariant.Danger
                )
            }
        ) {
            Text(
                pluralStringResource(R.plurals.signout_body, pending, pending),
                style = MaterialTheme.typography.bodyMedium
            )
        }
    }
}

/** A plain "Sign out" button with the unsent-writes warning; the failed-session screen uses it. */
@Composable
fun SignOutButton(model: FloorViewModel, onSignOut: () -> Unit) {
    SignOutHost(model, onSignOut) { request ->
        MqButton(stringResource(R.string.session_sign_out), request, variant = MqButtonVariant.Quiet)
    }
}
