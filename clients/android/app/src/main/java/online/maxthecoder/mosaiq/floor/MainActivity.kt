package online.maxthecoder.mosaiq.floor

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.LaunchedEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import online.maxthecoder.mosaiq.floor.alerts.AlertKind
import online.maxthecoder.mosaiq.floor.alerts.AlertTarget
import online.maxthecoder.mosaiq.floor.alerts.NotificationFactory
import online.maxthecoder.mosaiq.floor.ui.CustomerInsightsViewModel
import online.maxthecoder.mosaiq.floor.ui.CustomerRecordViewModel
import online.maxthecoder.mosaiq.floor.ui.FloorApp
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import online.maxthecoder.mosaiq.floor.ui.IdentifyViewModel
import online.maxthecoder.mosaiq.floor.ui.PromotionsViewModel
import online.maxthecoder.mosaiq.floor.ui.SalesViewModel
import online.maxthecoder.mosaiq.floor.ui.ScanViewModel
import online.maxthecoder.mosaiq.floor.ui.SessionState
import online.maxthecoder.mosaiq.floor.ui.SessionViewModel
import online.maxthecoder.mosaiq.floor.ui.inbox.InboxViewModel
import online.maxthecoder.mosaiq.floor.ui.indicators.IndicatorsViewModel
import online.maxthecoder.mosaiq.floor.ui.review.ReviewViewModel
import online.maxthecoder.mosaiq.floor.ui.shell.FloorHome
import online.maxthecoder.mosaiq.floor.ui.shell.SignOutButton

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        val graph = (application as FloorApplication).graph
        // A notification's tap; a restored activity has already handled the one that started it.
        if (savedInstanceState == null) openFrom(intent)
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
            val scan: ScanViewModel =
                viewModel(factory = viewModelFactory { initializer { ScanViewModel(graph) } })
            val sales: SalesViewModel =
                viewModel(factory = viewModelFactory { initializer { SalesViewModel(graph) } })
            val insights: CustomerInsightsViewModel =
                viewModel(factory = viewModelFactory { initializer { CustomerInsightsViewModel(graph) } })
            val identify: IdentifyViewModel =
                viewModel(factory = viewModelFactory { initializer { IdentifyViewModel(graph) } })
            val record: CustomerRecordViewModel =
                viewModel(factory = viewModelFactory { initializer { CustomerRecordViewModel(graph) } })
            val promotions: PromotionsViewModel =
                viewModel(factory = viewModelFactory { initializer { PromotionsViewModel(graph) } })
            val inbox: InboxViewModel =
                viewModel(factory = viewModelFactory { initializer { InboxViewModel(graph) } })
            val indicators: IndicatorsViewModel =
                viewModel(factory = viewModelFactory { initializer { IndicatorsViewModel(graph) } })
            val review: ReviewViewModel =
                viewModel(factory = viewModelFactory { initializer { ReviewViewModel(graph) } })
            val state = model.state.collectAsStateWithLifecycle()
            // Signing in again lets this user's queued writes go out, and starts the notification poll with a
            // pull at once (ADR-0071).
            LaunchedEffect(state.value is SessionState.SignedIn) {
                if (state.value is SessionState.SignedIn) {
                    graph.kick()
                    graph.alerts.start()
                }
            }
            FloorApp(
                state = state.value,
                onSignIn = model::signIn,
                onRetry = model::refreshProfile,
                onSignOut = model::signOut,
                floor = { signedIn ->
                    FloorHome(
                        signedIn,
                        floor,
                        scan,
                        sales,
                        insights,
                        identify,
                        record,
                        promotions,
                        model::signOut,
                        inbox,
                        indicators,
                        review
                    )
                },
                signOut = { SignOutButton(floor, model::signOut) }
            )
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        openFrom(intent)
    }

    /** Hands a tapped notification's kind and opaque id to the shell, which opens the updates screen. */
    private fun openFrom(intent: Intent?) {
        val kind =
            intent?.getStringExtra(NotificationFactory.EXTRA_KIND)?.let { name ->
                AlertKind.entries.firstOrNull { it.name == name }
            } ?: return
        (application as FloorApplication).graph.alerts.opened.value =
            AlertTarget(kind, intent.getStringExtra(NotificationFactory.EXTRA_SUBJECT))
    }
}
