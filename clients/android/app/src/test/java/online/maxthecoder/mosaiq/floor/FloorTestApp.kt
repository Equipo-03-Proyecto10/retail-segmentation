package online.maxthecoder.mosaiq.floor

import android.app.Application
import androidx.room.Room
import online.maxthecoder.mosaiq.floor.alerts.Notifier
import online.maxthecoder.mosaiq.floor.auth.InMemoryTokenStore
import online.maxthecoder.mosaiq.floor.auth.SessionEvents
import online.maxthecoder.mosaiq.floor.net.Network
import online.maxthecoder.mosaiq.floor.outbox.FloorDatabase
import online.maxthecoder.mosaiq.floor.session.InMemorySessionStore

/** Robolectric's application for the floor tests: a graph pointed at MockWebServer. */
class FloorTestApp :
    Application(),
    GraphProvider {
    override lateinit var graph: AppGraph

    lateinit var database: FloorDatabase

    fun build(
        baseUrl: String,
        timeoutSeconds: Long = 20,
        schedule: () -> Unit = {},
        notifier: Notifier = Notifier.NONE,
        schedulePoll: () -> Unit = {},
        cancelPoll: () -> Unit = {}
    ): AppGraph {
        database =
            Room.inMemoryDatabaseBuilder(this, FloorDatabase::class.java).allowMainThreadQueries().build()
        val tokens = InMemoryTokenStore()
        val events = SessionEvents()
        graph =
            AppGraph(
                tokens = tokens,
                session = InMemorySessionStore(),
                events = events,
                network = Network(baseUrl, tokens, events, timeoutSeconds),
                outbox = database.outbox(),
                location = { null },
                schedule = schedule,
                deviceLabel = "Robolectric",
                notified = database.notified(),
                notifier = notifier,
                schedulePoll = schedulePoll,
                cancelPoll = cancelPoll
            )
        return graph
    }
}
