package online.maxthecoder.mosaiq.floor

import android.app.Application
import androidx.room.Room
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

    fun build(baseUrl: String): AppGraph {
        database =
            Room.inMemoryDatabaseBuilder(this, FloorDatabase::class.java).allowMainThreadQueries().build()
        val tokens = InMemoryTokenStore()
        val events = SessionEvents()
        graph =
            AppGraph(
                tokens = tokens,
                session = InMemorySessionStore(),
                events = events,
                network = Network(baseUrl, tokens, events),
                outbox = database.outbox(),
                location = { null },
                schedule = {},
                deviceLabel = "Robolectric"
            )
        return graph
    }
}
