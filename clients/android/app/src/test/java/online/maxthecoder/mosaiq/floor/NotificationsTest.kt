package online.maxthecoder.mosaiq.floor

import android.app.Notification
import android.app.NotificationManager
import android.content.Context
import androidx.core.app.NotificationCompat
import androidx.room.Room
import androidx.sqlite.db.SupportSQLiteOpenHelper
import androidx.sqlite.db.framework.FrameworkSQLiteOpenHelperFactory
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.work.NetworkType
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.testing.WorkManagerTestInitHelper
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import online.maxthecoder.mosaiq.floor.alerts.AlertKind
import online.maxthecoder.mosaiq.floor.alerts.MIGRATION_1_2
import online.maxthecoder.mosaiq.floor.alerts.NotificationFactory
import online.maxthecoder.mosaiq.floor.alerts.NotificationPollWorker
import online.maxthecoder.mosaiq.floor.outbox.FloorDatabase
import online.maxthecoder.mosaiq.floor.outbox.Operation
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

/** ADR-0071: what a notification may say, the poll's schedule, and the database upgrade. */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class NotificationsTest {
    private val context: Context = ApplicationProvider.getApplicationContext()

    /** The test customer and staff member; none of this may reach a notification. */
    private val personal =
        listOf(
            "Lucia Hernandez",
            "lucia.hernandez@example.com",
            "+52 55 1234 5678",
            "c0ffee00-1111-4222-8333-444455556666",
            "Demo User 3",
            "user3@mosaiq-demo.com",
            "1,250.00"
        )

    private val shown =
        listOf(
            Notification.EXTRA_TITLE,
            Notification.EXTRA_TEXT,
            Notification.EXTRA_SUB_TEXT,
            Notification.EXTRA_BIG_TEXT
        )

    private fun texts(notification: Notification?): List<String> = listOfNotNull(notification).flatMap { n ->
        shown.mapNotNull { n.extras.getCharSequence(it)?.toString() } + listOfNotNull(n.tickerText?.toString())
    }

    @Test
    fun everyKindIsPrivateWithAPublicVersionLocalOnlyAndFreeOfPersonalData() {
        AlertKind.entries.filter { it != AlertKind.BASELINE }.forEach { kind ->
            val notification =
                NotificationFactory.buildNotification(context, kind, 3, "Loyal customers", "12")
            assertEquals("$kind is private", Notification.VISIBILITY_PRIVATE, notification.visibility)
            val public = notification.publicVersion
            assertNotNull("$kind has a public version", public)
            assertEquals(listOf("MOSAIQ", "New updates are available."), texts(public))
            assertTrue("$kind stays on the phone", NotificationCompat.getLocalOnly(notification))
            assertTrue("$kind's public version too", NotificationCompat.getLocalOnly(public!!))
            assertEquals("$kind has no large icon", null, notification.getLargeIcon())
            (texts(notification) + texts(public)).forEach { text ->
                personal.forEach { assertTrue("$kind shows [$it] in [$text]", it !in text) }
            }
            assertNotNull("$kind opens the app", notification.contentIntent)
        }
        assertEquals(
            listOf("MOSAIQ", "Campaign started: Loyal customers"),
            texts(NotificationFactory.buildNotification(context, AlertKind.CAMPAIGN_STARTED, 1, "Loyal customers"))
        )
        assertEquals(
            listOf("MOSAIQ", "3 new capture incidents"),
            texts(NotificationFactory.buildNotification(context, AlertKind.CAPTURE_INCIDENT, 3))
        )
        assertEquals(
            listOf("MOSAIQ", "1 queued item was refused"),
            texts(NotificationFactory.buildNotification(context, AlertKind.CAPTURE_ERROR, 1))
        )
    }

    @Test
    fun theFourChannelsArePrivateOnTheLockScreen() {
        NotificationFactory.createChannels(context)
        val manager = context.getSystemService(NotificationManager::class.java)
        val channels = manager.notificationChannels.associateBy { it.id }
        assertEquals(setOf("campaign_alerts", "capture_incidents", "capture_errors", "session"), channels.keys)
        channels.values.forEach {
            assertEquals("${it.id} is private", Notification.VISIBILITY_PRIVATE, it.lockscreenVisibility)
        }
        assertEquals(NotificationManager.IMPORTANCE_LOW, channels.getValue("session").importance)
    }

    @Test
    fun thePollIsUniqueEveryFifteenMinutesOnANetworkAndCancelledAtSignOut() {
        WorkManagerTestInitHelper.initializeTestWorkManager(context)
        val work = WorkManager.getInstance(context)
        NotificationPollWorker.scheduler(work)()
        NotificationPollWorker.scheduler(work)()

        val infos = work.getWorkInfosForUniqueWork(NotificationPollWorker.WORK_NAME).get()
        assertEquals("one schedule, however often sign-in runs", 1, infos.size)
        val info = infos.single()
        assertEquals(TimeUnit.MINUTES.toMillis(15), info.periodicityInfo?.repeatIntervalMillis)
        assertEquals(TimeUnit.MINUTES.toMillis(5), info.periodicityInfo?.flexIntervalMillis)
        assertEquals(NetworkType.CONNECTED, info.constraints.requiredNetworkType)
        assertTrue(info.constraints.requiresBatteryNotLow())
        assertTrue("not only while charging", !info.constraints.requiresCharging())

        NotificationPollWorker.canceller(work)()
        val after = work.getWorkInfosForUniqueWork(NotificationPollWorker.WORK_NAME).get().single()
        assertEquals(WorkInfo.State.CANCELLED, after.state)
    }

    @Test
    fun upgradingTheDatabaseKeepsTheQueuedWrites(): Unit = runBlocking {
        val name = "upgrade-test.db"
        context.deleteDatabase(name)
        // Version 1, as the previous build created it.
        val helper =
            FrameworkSQLiteOpenHelperFactory().create(
                SupportSQLiteOpenHelper.Configuration.builder(context)
                    .name(name)
                    .callback(
                        object : SupportSQLiteOpenHelper.Callback(1) {
                            override fun onCreate(db: androidx.sqlite.db.SupportSQLiteDatabase) {
                                db.execSQL(OUTBOX_V1)
                            }

                            override fun onUpgrade(
                                db: androidx.sqlite.db.SupportSQLiteDatabase,
                                oldVersion: Int,
                                newVersion: Int
                            ) = Unit
                        }
                    ).build()
            )
        helper.writableDatabase.execSQL(
            "INSERT INTO outbox (operation, path, body, clientId, userId, createdAt, state, attempts) " +
                "VALUES ('SALE', 'api/sales/v1/sales', '{}', 'k-1', 'A', 1, 'PENDING', 0)"
        )
        helper.close()

        val upgraded =
            Room.databaseBuilder(context, FloorDatabase::class.java, name).addMigrations(MIGRATION_1_2).build()
        val kept = upgraded.outbox().pendingFor("A")
        assertEquals(listOf(Operation.SALE), kept.map { it.operation })
        assertEquals(emptyList<String>(), upgraded.notified().subjects("A", AlertKind.CAMPAIGN_STARTED))
        upgraded.close()
        context.deleteDatabase(name)
    }

    private companion object {
        /** The outbox table exactly as version 1 created it. */
        const val OUTBOX_V1 =
            "CREATE TABLE IF NOT EXISTS `outbox` (`id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, " +
                "`operation` TEXT NOT NULL, `path` TEXT NOT NULL, `body` TEXT NOT NULL, " +
                "`clientId` TEXT NOT NULL, `userId` TEXT NOT NULL, `createdAt` INTEGER NOT NULL, " +
                "`state` TEXT NOT NULL, `attempts` INTEGER NOT NULL, `status` INTEGER, `code` TEXT, " +
                "`message` TEXT, `correlationId` TEXT, `responseBody` TEXT)"
    }
}
