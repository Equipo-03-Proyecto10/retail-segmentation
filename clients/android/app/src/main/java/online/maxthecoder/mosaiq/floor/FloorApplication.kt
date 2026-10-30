package online.maxthecoder.mosaiq.floor

import android.app.Application
import android.os.Build
import androidx.work.WorkManager
import online.maxthecoder.mosaiq.floor.auth.KeystoreTokenStore
import online.maxthecoder.mosaiq.floor.auth.SessionEvents
import online.maxthecoder.mosaiq.floor.auth.SessionRepository
import online.maxthecoder.mosaiq.floor.auth.TokenStore
import online.maxthecoder.mosaiq.floor.location.FusedLocationSource
import online.maxthecoder.mosaiq.floor.location.LocationSource
import online.maxthecoder.mosaiq.floor.net.Network
import online.maxthecoder.mosaiq.floor.outbox.FloorDatabase
import online.maxthecoder.mosaiq.floor.outbox.FloorWrites
import online.maxthecoder.mosaiq.floor.outbox.OutboxDao
import online.maxthecoder.mosaiq.floor.session.PrefsSessionStore
import online.maxthecoder.mosaiq.floor.session.SessionStore

/** Everything the screens and the floor-write queue share, built once. */
class AppGraph(
    val tokens: TokenStore,
    val session: SessionStore,
    val events: SessionEvents,
    val network: Network,
    val outbox: OutboxDao,
    val location: LocationSource,
    schedule: () -> Unit,
    deviceLabel: String
) {
    val writes = FloorWrites(outbox, session, schedule)
    val sessions = SessionRepository(network, tokens, deviceLabel, session)

    /** Resends whatever the signed-in user left queued. */
    val kick: () -> Unit = schedule
}

/** Where the floor-write queue's worker finds the graph; tests provide their own. */
interface GraphProvider {
    val graph: AppGraph
}

class FloorApplication :
    Application(),
    GraphProvider {
    override lateinit var graph: AppGraph
        private set

    override fun onCreate() {
        super.onCreate()
        AppText.install(this)
        val tokens = KeystoreTokenStore(this)
        val events = SessionEvents()
        graph =
            AppGraph(
                tokens = tokens,
                session = PrefsSessionStore(this),
                events = events,
                network = Network(BuildConfig.BASE_URL, tokens, events),
                outbox = FloorDatabase.open(this).outbox(),
                location = FusedLocationSource(this),
                schedule = FloorWrites.scheduler(WorkManager.getInstance(this)),
                deviceLabel = "Android ${Build.MANUFACTURER} ${Build.MODEL}".take(DEVICE_LABEL_MAX)
            )
        // Rows left from before a restart go out once the network is back.
        graph.kick()
    }

    private companion object {
        /** `device_label` maxLength in contracts/auth/openapi.yaml. */
        const val DEVICE_LABEL_MAX = 100
    }
}
