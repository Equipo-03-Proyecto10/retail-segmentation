package online.maxthecoder.mosaiq.floor.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import online.maxthecoder.mosaiq.floor.net.ApiError

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
    MaterialTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            // Bars, cutout and keyboard are kept clear of; the title stays put and the rest scrolls.
            Column(
                modifier = Modifier.safeDrawingPadding().padding(24.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp)
            ) {
                Text("MOSAIQ Floor", style = MaterialTheme.typography.headlineMedium)
                if (state is SessionState.SignedIn) {
                    // The floor's tabs scroll on their own (lists and forms), so they get a bounded box.
                    Box(modifier = Modifier.weight(1f)) {
                        floor?.invoke(state) ?: Column { SignedInPanel(state, onSignOut) }
                    }
                } else {
                    Column(
                        modifier = Modifier.weight(1f).verticalScroll(rememberScrollState()),
                        verticalArrangement = Arrangement.spacedBy(16.dp)
                    ) {
                        when (state) {
                            is SessionState.SignIn -> SignInForm(state, email, { email = it }, onSignIn)
                            SessionState.Loading -> CircularProgressIndicator()
                            is SessionState.Failed -> {
                                ErrorPanel(state.error)
                                Button(onClick = onRetry) { Text("Try again") }
                                // The floor's sign-out warns about queued writes first.
                                signOut?.invoke() ?: OutlinedButton(onClick = onSignOut) { Text("Sign out") }
                            }
                            is SessionState.SignedIn -> Unit
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SignInForm(
    state: SessionState.SignIn,
    email: String,
    onEmail: (String) -> Unit,
    onSignIn: (String, String) -> Unit
) {
    // A password is never saved across process death.
    var password by remember { mutableStateOf("") }
    state.notice?.let { Text(it, color = MaterialTheme.colorScheme.primary) }
    state.error?.let { ErrorPanel(it) }
    OutlinedTextField(
        value = email,
        onValueChange = onEmail,
        label = { Text("Email") },
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
        modifier = Modifier.fillMaxWidth()
    )
    OutlinedTextField(
        value = password,
        onValueChange = { password = it },
        label = { Text("Password") },
        singleLine = true,
        visualTransformation = PasswordVisualTransformation(),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
        modifier = Modifier.fillMaxWidth()
    )
    Button(
        onClick = { onSignIn(email, password) },
        enabled = email.isNotBlank() && password.isNotEmpty(),
        modifier = Modifier.fillMaxWidth()
    ) { Text("Sign in") }
}

@Composable
private fun SignedInPanel(state: SessionState.SignedIn, onSignOut: () -> Unit) {
    Text("Signed in as", style = MaterialTheme.typography.labelLarge)
    Text(state.name, style = MaterialTheme.typography.titleLarge)
    Text(state.role, style = MaterialTheme.typography.bodyLarge)
    OutlinedButton(onClick = onSignOut) { Text("Sign out") }
}

/** Every refusal shows its code, its message and the request's correlation id. */
@Composable
fun ErrorPanel(error: ApiError) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(error.message, color = MaterialTheme.colorScheme.error)
        Text("Code: ${error.code}", style = MaterialTheme.typography.bodySmall)
        if (error.code == "future_dated") Text("Check the phone's date, time and time zone.")
        error.details.forEach { detail ->
            Text("${detail.field}: ${detail.code}", style = MaterialTheme.typography.bodySmall)
        }
        Text("Correlation ID: ${error.correlationId ?: "none"}", style = MaterialTheme.typography.bodySmall)
    }
}
