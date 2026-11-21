package online.maxthecoder.mosaiq.floor.alerts

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import kotlinx.coroutines.flow.Flow

/**
 * What a notification is about (ADR-0071). Closed: the notification builder takes one of these, never text.
 * [BASELINE] is not a notification: its rows mark that the first, silent poll of a source has run.
 */
enum class AlertKind { CAMPAIGN_STARTED, CAMPAIGN_ENDING, CAPTURE_INCIDENT, CAPTURE_ERROR, SESSION_ENDED, BASELINE }

/**
 * One thing this user has already been told about (ADR-0071's `notified_item`): ids and times, no text.
 * [subjectId] is a `campaign_id`, an `incident_id` or a queued write's client id. [subjectAt] is the
 * server's `recorded_at` of an incident, the poll's watermark, and null otherwise. [readAt] is set when the
 * user opens the updates screen, so the badge counts what is still unread.
 */
@Entity(tableName = "notified_item", primaryKeys = ["userId", "kind", "subjectId"])
data class NotifiedItem(
    val userId: String,
    val kind: AlertKind,
    val subjectId: String,
    val notifiedAt: Long,
    val readAt: Long? = null,
    val subjectAt: Long? = null
)

@Dao
interface NotifiedItemDao {
    /** `-1` when the row already exists: that is what makes every alert fire at most once. */
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(item: NotifiedItem): Long

    @Query("SELECT subjectId FROM notified_item WHERE userId = :userId AND kind = :kind")
    suspend fun subjects(userId: String, kind: AlertKind): List<String>

    @Query("SELECT count(*) FROM notified_item WHERE userId = :userId AND kind = :kind AND subjectId = :subjectId")
    suspend fun has(userId: String, kind: AlertKind, subjectId: String): Int

    @Query("SELECT count(*) FROM notified_item WHERE userId = :userId AND kind = :kind AND readAt IS NULL")
    suspend fun countUnread(userId: String, kind: AlertKind): Int

    @Query("SELECT count(*) FROM notified_item WHERE userId = :userId AND kind != 'BASELINE' AND readAt IS NULL")
    fun observeUnread(userId: String): Flow<Int>

    @Query("SELECT * FROM notified_item WHERE userId = :userId AND kind != 'BASELINE' AND readAt IS NULL")
    suspend fun unread(userId: String): List<NotifiedItem>

    @Query("SELECT max(subjectAt) FROM notified_item WHERE userId = :userId AND kind = 'CAPTURE_INCIDENT'")
    suspend fun newestIncidentAt(userId: String): Long?

    @Query(
        "SELECT min(subjectAt) FROM notified_item WHERE userId = :userId AND kind = 'CAPTURE_INCIDENT' " +
            "AND readAt IS NULL"
    )
    suspend fun oldestUnreadIncidentAt(userId: String): Long?

    @Query("UPDATE notified_item SET readAt = :at WHERE userId = :userId AND readAt IS NULL")
    suspend fun markRead(userId: String, at: Long)

    @Query("DELETE FROM notified_item WHERE userId = :userId AND kind = :kind")
    suspend fun deleteKind(userId: String, kind: AlertKind)

    /** Sign-out forgets everything this user was told. */
    @Query("DELETE FROM notified_item WHERE userId = :userId")
    suspend fun deleteFor(userId: String)
}

/** Version 2 adds `notified_item`; the outbox and its queued writes are kept as they are. */
val MIGRATION_1_2 =
    object : Migration(1, 2) {
        override fun migrate(db: SupportSQLiteDatabase) {
            db.execSQL(
                "CREATE TABLE IF NOT EXISTS `notified_item` (`userId` TEXT NOT NULL, `kind` TEXT NOT NULL, " +
                    "`subjectId` TEXT NOT NULL, `notifiedAt` INTEGER NOT NULL, `readAt` INTEGER, " +
                    "`subjectAt` INTEGER, PRIMARY KEY(`userId`, `kind`, `subjectId`))"
            )
        }
    }
