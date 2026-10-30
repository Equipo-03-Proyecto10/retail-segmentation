package online.maxthecoder.mosaiq.floor

import androidx.test.ext.junit.runners.AndroidJUnit4
import java.io.IOException
import kotlinx.coroutines.runBlocking
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import online.maxthecoder.mosaiq.floor.net.Answer
import online.maxthecoder.mosaiq.floor.net.call
import online.maxthecoder.mosaiq.floor.net.totalPages
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config
import retrofit2.Response

/** The shared call wrapper and pager math. */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class AnswerHelpersTest {
    @Test
    fun pagerRoundsUpAndNeverDropsBelowOne() {
        assertEquals(1, totalPages(0, 20))
        assertEquals(1, totalPages(20, 20))
        assertEquals(2, totalPages(21, 20))
        assertEquals(3, totalPages(41, 20))
        assertEquals(1, totalPages(5, 0))
    }

    @Test
    fun successCarriesTheBody() = runBlocking {
        assertEquals(Answer.Body("ok"), call { Response.success("ok") })
    }

    @Test
    fun refusalCarriesTheStatus() = runBlocking {
        val failure = Response.error<String>(404, "{}".toResponseBody("application/json".toMediaType()))
        val answer = call { failure }
        assertEquals(404, (answer as Answer.Refused).error.status)
    }

    @Test
    fun transportFailureIsUnreachable() = runBlocking {
        val answer = call<String> { throw IOException("down") }
        assertEquals("unreachable", (answer as Answer.Refused).error.code)
        assertTrue(answer.error.status == 0)
    }
}
