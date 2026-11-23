package online.maxthecoder.mosaiq.floor

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.work.ListenableWorker
import androidx.work.testing.TestListenableWorkerBuilder
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.time.OffsetDateTime
import kotlinx.coroutines.runBlocking
import okhttp3.Interceptor
import online.maxthecoder.mosaiq.floor.auth.InMemoryTokenStore
import online.maxthecoder.mosaiq.floor.auth.SessionEvents
import online.maxthecoder.mosaiq.floor.auth.SessionResult
import online.maxthecoder.mosaiq.floor.net.Customer
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
                "instrumented stack test",
                notified = database.notified()
            )
        app.graph = graph
    }

    @After
    fun stop() {
        if (::database.isInitialized) database.close()
    }

    /** The signed-in manager's pick of a customer, an in-stock product and the in-store channel. */
    private class Counter(
        val customer: Customer,
        val store: Store,
        val product: Product,
        val channelId: Int
    )

    private suspend fun readyToSell(): Counter {
        val signIn = graph.sessions.signIn(EMAIL, PASSWORD)
        assertTrue("sign-in answered $signIn", signIn is SessionResult.SignedIn)
        val network = graph.network
        val channel =
            network.sales.listChannels().body()!!.channels.single { it.name == "physical_store" }
        val customer = network.customers.searchCustomers().body()!!.customers.first()
        val (store, product) = stockedPair(network)
        graph.session.choose(ChosenStore(store.storeId, store.name))
        return Counter(customer, store, product, channel.channelId)
    }

    private suspend fun queue(counter: Counter): Long = graph.writes.queueSale(
        counter.customer.customerId,
        counter.store.storeId,
        counter.channelId,
        listOf(SaleLineRequest(counter.product.productId, 1, counter.product.listPrice)),
        OffsetDateTime.now().minusMinutes(1)
    )

    @Test
    fun aSaleQueuedOnTheFloorIsRecordedOnceAndReadsBack() = runBlocking {
        val counter = readyToSell()
        val network = graph.network
        val (customer, store, product) = Triple(counter.customer, counter.store, counter.product)

        val id = queue(counter)
        assertEquals(ListenableWorker.Result.success(), work())
        val row = graph.outbox.find(id)!!
        assertEquals(RowState.DONE, row.state)
        assertEquals(201, row.status)
        val recorded = json.decodeFromString(Sale.serializer(), row.responseBody!!)

        val read = network.sales.getSale(recorded.saleId).body()!!
        assertEquals(customer.customerId, read.customerId)
        assertEquals(store.storeId, read.storeId)
        assertEquals(counter.channelId, read.channelId)
        assertEquals(listOf(product.productId), read.lines.map { it.productId })

        // A second pass has nothing left to send: still one sale.
        assertEquals(ListenableWorker.Result.success(), work())
        val sales = network.sales.listSales(customer.customerId, size = 100).body()!!.sales
        assertEquals(1, sales.count { it.saleId == recorded.saleId })
    }

    /**
     * F27-13: the real services commit the sale and the connection drops before the answer reaches the phone.
     * An interceptor lets the first sale request through, closes its response and fails the call, as a cut
     * line does; the queue keeps the sale pending, and the replay under the same Idempotency-Key is answered
     * 200 with the sale the first request made, so the customer has exactly one more sale.
     */
    @Test
    fun aSaleWhoseAnswerIsLostIsReplayedAsTheSameSaleAndNeverTwice() = runBlocking {
        val counter = readyToSell()
        val network = graph.network
        val before = network.sales.listSales(counter.customer.customerId, size = 100).body()!!.sales.map { it.saleId }
        val id = queue(counter)
        val key = graph.outbox.find(id)!!.clientId

        val seen = mutableListOf<String?>()
        val cutting =
            Interceptor { chain ->
                val response = chain.proceed(chain.request())
                val sale = chain.request().header("Idempotency-Key")
                if (sale != null) seen += sale
                if (sale != null && seen.size == 1) {
                    response.close()
                    throw IOException("the line was cut after the server answered")
                }
                response
            }
        val client = network.client.newBuilder().addInterceptor(cutting).build()
        val drain: suspend () -> ListenableWorker.Result = {
            OutboxWorker.drain(graph.outbox, graph.session, graph.tokens, client, network.gateway)
        }

        assertEquals(ListenableWorker.Result.retry(), drain())
        // Never claimed recorded: the phone holds no answer, and the row is still pending.
        assertEquals(RowState.PENDING, graph.outbox.find(id)!!.state)
        assertEquals(null, graph.outbox.find(id)!!.responseBody)
        val committed =
            network.sales.listSales(counter.customer.customerId, size = 100).body()!!.sales.map { it.saleId }
        assertEquals("the server committed the sale", before.size + 1, committed.size)

        assertEquals(ListenableWorker.Result.success(), drain())
        assertEquals(listOf(key, key), seen)
        val row = graph.outbox.find(id)!!
        assertEquals(RowState.DONE, row.state)
        assertEquals(200, row.status)
        assertEquals("idempotent_replayed", row.code)
        val recorded = json.decodeFromString(Sale.serializer(), row.responseBody!!)
        assertEquals((committed - before.toSet()).single(), recorded.saleId)
        val after = network.sales.listSales(counter.customer.customerId, size = 100).body()!!.sales
        assertEquals("one sale, not two", before.size + 1, after.size)
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
