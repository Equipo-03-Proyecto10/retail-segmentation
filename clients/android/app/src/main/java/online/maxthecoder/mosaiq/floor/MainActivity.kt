package online.maxthecoder.mosaiq.floor

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.LaunchedEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import online.maxthecoder.mosaiq.floor.ui.FloorApp
import online.maxthecoder.mosaiq.floor.ui.FloorHome
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import online.maxthecoder.mosaiq.floor.ui.SessionState
import online.maxthecoder.mosaiq.floor.ui.SessionViewModel
import online.maxthecoder.mosaiq.floor.ui.SignOutButton

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val graph = (application as FloorApplication).graph
        setContent {
            val model: SessionViewModel =
                viewModel(
                    factory =
                    viewModelFactory {
                        initializer { SessionViewModel(graph.sessions, graph.events) }
                    }
                )
            val floor: FloorViewModel =
                viewModel(factory = viewModelFactory { initializer { FloorViewModel(graph) } })
            val state = model.state.collectAsStateWithLifecycle()
            // Signing in again lets this user's queued writes go out.
            LaunchedEffect(state.value is SessionState.SignedIn) {
                if (state.value is SessionState.SignedIn) graph.kick()
            }
            FloorApp(
                state = state.value,
                onSignIn = model::signIn,
                onRetry = model::refreshProfile,
                onSignOut = model::signOut,
                floor = { signedIn -> FloorHome(signedIn, floor, model::signOut) },
                signOut = { SignOutButton(floor, model::signOut) }
            )
        }
    }
}
