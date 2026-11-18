package online.maxthecoder.mosaiq.floor

import android.app.Application
import android.os.Build
import androidx.work.WorkManager
import online.maxthecoder.mosaiq.floor.alerts.Alerts
import online.maxthecoder.mosaiq.floor.alerts.NotificationPollWorker
import online.maxthecoder.mosaiq.floor.alerts.NotifiedItemDao
import online.maxthecoder.mosaiq.floor.alerts.Notifier
import online.maxthecoder.mosaiq.floor.alerts.SystemNotifier
import online.maxthecoder.mosaiq.floor.auth.KeystoreTokenStore
import online.maxthecoder.mosaiq.floor.auth.SessionEvents
import online.maxthecoder.mosaiq.floor.auth.SessionRepository
import online.maxthecoder.mosaiq.floor.auth.TokenStore
import online.maxthecoder.mosaiq.floor.location.FusedLocationSource
import online.maxthecoder.mosaiq.floor.location.LocationSource
import online.maxthecoder.mosaiq.floor.net.Network
import online.maxthecoder.mosaiq.floor.outbox.FloorDatabase
import online.maxthecoder.mosaiq.floor.outbox.FloorWrites
import online.maxthecoder.mosaiq.floor.outbox.InFlight
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
    deviceLabel: String,
    sendNow: () -> Unit = schedule,
    notified: NotifiedItemDao,
    notifier: Notifier = Notifier.NONE,
    schedulePoll: () -> Unit = {},
    cancelPoll: () -> Unit = {}
) {
    val writes = FloorWrites(outbox, session, schedule)

    /** Notifications (ADR-0071): the periodic pull, capture errors and their incident reports. */
    val alerts = Alerts(network, tokens, session, notified, writes, notifier, schedulePoll, cancelPoll)

    /** Which queued rows are being sent right now, for the outbox's "Sending" badge. */
    val inFlight = InFlight()
    val sessions = SessionRepository(network, tokens, deviceLabel, session) { alerts.signedOut(it) }

    /** Resends whatever the signed-in user left queued. */
    val kick: () -> Unit = schedule

    /** The outbox's manual send: starts a drain now, outside the chain's backoff. */
    val sendNow: () -> Unit = sendNow
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
        val database = FloorDatabase.open(this)
        val work = WorkManager.getInstance(this)
        graph =
            AppGraph(
                tokens = tokens,
                session = PrefsSessionStore(this),
                events = events,
                network = Network(BuildConfig.BASE_URL, tokens, events),
                outbox = database.outbox(),
                location = FusedLocationSource(this),
                schedule = FloorWrites.scheduler(work),
                sendNow = FloorWrites.immediate(work),
                deviceLabel = "Android ${Build.MANUFACTURER} ${Build.MODEL}".take(DEVICE_LABEL_MAX),
                notified = database.notified(),
                notifier = SystemNotifier(this),
                schedulePoll = NotificationPollWorker.scheduler(work),
                cancelPoll = NotificationPollWorker.canceller(work)
            )
        // Rows left from before a restart go out once the network is back.
        graph.kick()
    }

    private companion object {
        /** `device_label` maxLength in contracts/auth/openapi.yaml. */
        const val DEVICE_LABEL_MAX = 100
    }
}
