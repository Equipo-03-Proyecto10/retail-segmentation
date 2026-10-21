package online.maxthecoder.mosaiq.floor

import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import online.maxthecoder.mosaiq.floor.auth.KeystoreTokenStore
import online.maxthecoder.mosaiq.floor.auth.SessionEvents
import online.maxthecoder.mosaiq.floor.auth.SessionRepository
import online.maxthecoder.mosaiq.floor.net.Network
import online.maxthecoder.mosaiq.floor.ui.FloorApp
import online.maxthecoder.mosaiq.floor.ui.SessionViewModel

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val context = applicationContext
        setContent {
            val model: SessionViewModel =
                viewModel(
                    factory =
                    viewModelFactory {
                        initializer {
                            val tokens = KeystoreTokenStore(context)
                            val events = SessionEvents()
                            val network = Network(BuildConfig.BASE_URL, tokens, events)
                            SessionViewModel(SessionRepository(network, tokens, deviceLabel()), events)
                        }
                    }
                )
            val state = model.state.collectAsStateWithLifecycle()
            FloorApp(
                state = state.value,
                onSignIn = model::signIn,
                onRetry = model::refreshProfile,
                onSignOut = model::signOut
            )
        }
    }

    private fun deviceLabel(): String = "Android ${Build.MANUFACTURER} ${Build.MODEL}".take(DEVICE_LABEL_MAX)

    private companion object {
        /** `device_label` maxLength in contracts/auth/openapi.yaml. */
        const val DEVICE_LABEL_MAX = 100
    }
}
