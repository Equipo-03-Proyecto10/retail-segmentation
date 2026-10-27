package online.maxthecoder.mosaiq.floor.ui.shell

import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
        AlertDialog(
            onDismissRequest = { pending = 0 },
            title = { Text(stringResource(R.string.signout_title)) },
            text = {
                Text(pluralStringResource(R.plurals.signout_body, pending, pending))
            },
            confirmButton = {
                TextButton(onClick = {
                    pending = 0
                    onSignOut()
                }) { Text(stringResource(R.string.signout_confirm)) }
            },
            dismissButton = { TextButton(onClick = { pending = 0 }) { Text(stringResource(R.string.signout_stay)) } }
        )
    }
}

/** A plain "Sign out" button with the unsent-writes warning; the failed-session screen uses it. */
@Composable
fun SignOutButton(model: FloorViewModel, onSignOut: () -> Unit) {
    SignOutHost(model, onSignOut) { request ->
        TextButton(onClick = request) { Text(stringResource(R.string.session_sign_out)) }
    }
}
