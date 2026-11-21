package online.maxthecoder.mosaiq.floor

import online.maxthecoder.mosaiq.floor.net.ApiError
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * F27-13: every screen that shows a connection error says it in plain words and says what happens next.
 * The words are written once, in [ApiError], so no screen can show a bare status line or a gateway's text.
 */
class ApiErrorMessagesTest {
    private fun answered(status: Int, body: String? = null, retryAfter: String? = null) =
        ApiError.from(status, body, "corr-1", retryAfter)

    private val gatewayText = """{"code":"bad_gateway","message":"upstream connect error or disconnect/reset"}"""

    @Test
    fun noAnswerAtAllSaysTheWorkIsKeptAndSentLater() {
        val message = ApiError.unreachable(null).message
        assertTrue(message, message.startsWith("No connection."))
        assertTrue(message, "kept on this phone and sent when you are back online" in message)
        assertTrue(message, "try again" in message)
    }

    @Test
    fun aGatewayFaultNeverShowsTheGatewaysOwnText() {
        for (status in listOf(502, 503, 504)) {
            val error = answered(status, gatewayText)
            assertEquals(status, error.status)
            assertEquals("bad_gateway", error.code)
            assertTrue("$status: ${error.message}", "not answering right now" in error.message)
            assertTrue(error.message, "sent when it answers" in error.message)
            assertFalse(error.message, "upstream" in error.message)
        }
    }

    @Test
    fun aServerFaultSaysToTryAgainAndWhatToGiveSupport() {
        val error = answered(500, """{"code":"internal","message":"NullPointerException at Foo.kt:12"}""")
        assertEquals("internal", error.code)
        assertTrue(error.message, "Try again in a moment" in error.message)
        assertTrue(error.message, "code under Details" in error.message)
        assertFalse(error.message, "NullPointer" in error.message)
    }

    @Test
    fun aRateLimitSaysHowLongToWaitWhenTheServiceSaysSo() {
        assertEquals("Too many requests at once. Wait 1 second, then try again.", answered(429, null, "1").message)
        assertEquals("Too many requests at once. Wait 30 seconds, then try again.", answered(429, null, "30").message)
        assertEquals("Too many requests at once. Wait a few seconds, then try again.", answered(429).message)
    }

    @Test
    fun aRefusalKeepsTheServicesOwnWordsBecauseTheyNameWhatToFix() {
        val error = answered(422, """{"code":"invalid_body","message":"The name is required."}""")
        assertEquals("The name is required.", error.message)
        assertEquals("invalid_body", error.code)
    }

    @Test
    fun anAnswerWithNoEnvelopeIsNotABareStatusLine() {
        val error = answered(404)
        assertEquals("http_404", error.code)
        assertFalse(error.message, error.message.startsWith("The service answered"))
        assertTrue(error.message, "Try again" in error.message)
        assertTrue(error.message, "404" in error.message)
    }

    @Test
    fun theCorrelationIdStaysForSupport() {
        assertEquals("corr-1", answered(503).correlationId)
    }
}
