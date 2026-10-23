package online.maxthecoder.mosaiq.floor

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.work.ListenableWorker
import androidx.work.testing.TestListenableWorkerBuilder
import java.net.HttpURLConnection
import java.net.URL
import java.time.OffsetDateTime
import kotlinx.coroutines.runBlocking
import online.maxthecoder.mosaiq.floor.auth.InMemoryTokenStore
import online.maxthecoder.mosaiq.floor.auth.SessionEvents
import online.maxthecoder.mosaiq.floor.auth.SessionResult
import online.maxthecoder.mosaiq.floor.net.Network
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.net.Sale
import online.maxthecoder.mosaiq.floor.net.SaleLineRequest
import online.maxthecoder.mosaiq.floor.net.Store
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.outbox.FloorDatabase
import online.maxthecoder.mosaiq.floor.outbox.OutboxWorker
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.session.InMemorySessionStore
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * One counter sale through the app's own outbox, against the real services
 * behind the local stack's gateway, the debug build's base URL (F17-06). It
 * skips when the gateway does not answer, unless the run passes
 * `-e stack required`, as the emulator job does: there it fails instead.
 */
@RunWith(AndroidJUnit4::class)
class StackSaleInstrumentedTest {
    private lateinit var app: SalesTestApplication
    private lateinit var database: FloorDatabase
    private lateinit var graph: AppGraph

    @Before
    fun start() {
        val required = InstrumentationRegistry.getArguments().getString("stack") == "required"
        val reachable = answers(BuildConfig.BASE_URL + "api/auth/health/ready")
        if (required) {
            assertTrue("the gateway at ${BuildConfig.BASE_URL} does not answer", reachable)
        } else {
            assumeTrue("no local stack at ${BuildConfig.BASE_URL}", reachable)
        }
        app = ApplicationProvider.getApplicationContext()
        database = Room.inMemoryDatabaseBuilder(app, FloorDatabase::class.java).build()
        val tokens = InMemoryTokenStore()
        val events = SessionEvents()
        graph =
            AppGraph(
                tokens,
                InMemorySessionStore(),
                events,
                Network(BuildConfig.BASE_URL, tokens, events),
                database.outbox(),
                { null },
                {},
                "instrumented stack test"
            )
        app.graph = graph
    }

    @After
    fun stop() {
        if (::database.isInitialized) database.close()
    }

    @Test
    fun aSaleQueuedOnTheFloorIsRecordedOnceAndReadsBack() = runBlocking {
        val signIn = graph.sessions.signIn(EMAIL, PASSWORD)
        assertTrue("sign-in answered $signIn", signIn is SessionResult.SignedIn)
        val network = graph.network
        val channel =
            network.sales.listChannels().body()!!.channels.single { it.name == "physical_store" }
        val customer = network.customers.searchCustomers().body()!!.customers.first()
        val (store, product) = stockedPair(network)
        graph.session.choose(ChosenStore(store.storeId, store.name))

        val id =
            graph.writes.queueSale(
                customer.customerId,
                store.storeId,
                channel.channelId,
                listOf(SaleLineRequest(product.productId, 1, product.listPrice)),
                OffsetDateTime.now().minusMinutes(1)
            )
        assertEquals(ListenableWorker.Result.success(), work())
        val row = graph.outbox.find(id)!!
        assertEquals(RowState.DONE, row.state)
        assertEquals(201, row.status)
        val recorded = json.decodeFromString(Sale.serializer(), row.responseBody!!)

        val read = network.sales.getSale(recorded.saleId).body()!!
        assertEquals(customer.customerId, read.customerId)
        assertEquals(store.storeId, read.storeId)
        assertEquals(channel.channelId, read.channelId)
        assertEquals(listOf(product.productId), read.lines.map { it.productId })

        // A second pass has nothing left to send: still one sale.
        assertEquals(ListenableWorker.Result.success(), work())
        val sales = network.sales.listSales(customer.customerId, size = 100).body()!!.sales
        assertEquals(1, sales.count { it.saleId == recorded.saleId })
    }

    /** An active store and an active, priced product it has in stock. */
    private suspend fun stockedPair(network: Network): Pair<Store, Product> {
        val stores = network.stores.listStores().body()!!.stores
        // The search needs a term: a few common letters find the seed's products by name.
        val products =
            listOf("e", "o", "a")
                .flatMap { network.products.listProducts(it, size = 50).body()!!.products }
                .distinctBy { it.productId }
                .filter { it.isActive && it.listPrice.toBigDecimal().signum() > 0 }
        for (store in stores) {
            for (product in products) {
                val level = network.stock.getStockLevel(store.storeId, product.productId)
                if (level.isSuccessful && level.body()!!.quantityOnHand > 0) return store to product
            }
        }
        error("no store of ${stores.size} has any of ${products.size} products in stock")
    }

    private suspend fun work(): ListenableWorker.Result =
        TestListenableWorkerBuilder<OutboxWorker>(app).build().doWork()

    private fun answers(url: String): Boolean = try {
        val connection = URL(url).openConnection() as HttpURLConnection
        connection.connectTimeout = 3_000
        connection.readTimeout = 3_000
        try {
            connection.responseCode in 200..299
        } finally {
            connection.disconnect()
        }
    } catch (_: java.io.IOException) {
        false
    }

    private companion object {
        // A STORE_MANAGER of the seed, with the seed's published demonstration password (ADR-0008).
        const val EMAIL = "user3@mosaiq-demo.com"
        const val PASSWORD = "Password123!"
    }
}
