package online.maxthecoder.mosaiq.floor.outbox

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import kotlinx.coroutines.flow.Flow

/** What a queued write is; the sales story adds its own. */
enum class Operation { CHECK_IN, QR_SCAN, SALE }

/** Where a write stands. A refused one is kept until the user dismisses it. */
enum class RowState { PENDING, DONE, REFUSED }

/**
 * One floor write (ADR-0041): its body and its client id were fixed at the tap
 * and never change, so every resend is the same request. `userId` is the user
 * who made it; the row is sent only under that user's session.
 */
@Entity(tableName = "outbox")
data class OutboxRow(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val operation: Operation,
    val path: String,
    val body: String,
    val clientId: String,
    val userId: String,
    val createdAt: Long,
    val state: RowState = RowState.PENDING,
    val attempts: Int = 0,
    val status: Int? = null,
    val code: String? = null,
    val message: String? = null,
    val correlationId: String? = null,
    val responseBody: String? = null
)

@Dao
interface OutboxDao {
    @Insert
    suspend fun insert(row: OutboxRow): Long

    @Query("SELECT * FROM outbox WHERE state = 'PENDING' AND userId = :userId ORDER BY id")
    suspend fun pendingFor(userId: String): List<OutboxRow>

    @Query("SELECT count(*) FROM outbox WHERE state = 'PENDING' AND userId = :userId")
    suspend fun countPendingFor(userId: String): Int

    @Query("SELECT * FROM outbox WHERE id = :id")
    suspend fun find(id: Long): OutboxRow?

    @Query("SELECT * FROM outbox WHERE id = :id")
    fun observe(id: Long): Flow<OutboxRow?>

    /** What the outbox screen lists: refused rows, and rows still waiting. */
    @Query("SELECT * FROM outbox WHERE state != 'DONE' ORDER BY id")
    fun observeOpen(): Flow<List<OutboxRow>>

    @Query("SELECT * FROM outbox WHERE operation = 'SALE' AND userId = :userId ORDER BY id DESC")
    fun observeSales(userId: String): Flow<List<OutboxRow>>

    @Query("SELECT * FROM outbox WHERE operation = 'CHECK_IN' AND userId = :userId ORDER BY id DESC LIMIT 1")
    suspend fun latestCheckIn(userId: String): OutboxRow?

    @Query("UPDATE outbox SET attempts = attempts + 1 WHERE id = :id")
    suspend fun countAttempt(id: Long)

    @Query(
        "UPDATE outbox SET state = 'DONE', status = :status, responseBody = :body, " +
            "correlationId = :correlationId, code = :code WHERE id = :id AND state = 'PENDING'"
    )
    suspend fun markDone(id: Long, status: Int, body: String?, correlationId: String?, code: String? = null)

    @Query(
        "UPDATE outbox SET state = 'REFUSED', status = :status, code = :code, message = :message, " +
            "correlationId = :correlationId WHERE id = :id AND state = 'PENDING'"
    )
    suspend fun markRefused(id: Long, status: Int, code: String, message: String, correlationId: String?)

    /** Only a refused row can be dismissed: a pending one is never dropped. */
    @Query("DELETE FROM outbox WHERE id = :id AND state = 'REFUSED'")
    suspend fun dismiss(id: Long): Int
}

@Database(entities = [OutboxRow::class], version = 1, exportSchema = false)
abstract class FloorDatabase : RoomDatabase() {
    abstract fun outbox(): OutboxDao

    companion object {
        fun open(context: Context): FloorDatabase =
            Room.databaseBuilder(context, FloorDatabase::class.java, "floor.db").build()
    }
}
