package online.maxthecoder.mosaiq.floor

import okhttp3.mockwebserver.MockResponse
import online.maxthecoder.mosaiq.floor.auth.InMemoryTokenStore
import online.maxthecoder.mosaiq.floor.auth.SessionEvents
import online.maxthecoder.mosaiq.floor.auth.SessionRepository
import online.maxthecoder.mosaiq.floor.auth.TokenStore
import online.maxthecoder.mosaiq.floor.net.Network

const val SIGN_IN = "/api/auth/v1/sessions"
const val REFRESH = "/api/auth/v1/sessions/refresh"
const val CURRENT_SESSION = "/api/auth/v1/sessions/current"
const val ME = "/api/users/v1/users/me"

fun tokens(access: String, refresh: String): String =
    """{"access_token":"$access","refresh_token":"$refresh","token_type":"Bearer",""" +
        """"expires_in":600,"expires_at":"2026-10-21T12:10:00Z"}"""

fun user(role: String): String = """{"user_id":"11111111-1111-1111-1111-000000000003","name":"Demo User 3",""" +
    """"email":"user3@mosaiq-demo.com","role_code":"$role","is_active":true,""" +
    """"created_at":"2026-10-18T00:00:00Z","added_in_a_later_minor":"ignored"}"""

fun error(status: Int, code: String, message: String = "Refused: $code."): MockResponse = MockResponse()
    .setResponseCode(status)
    .setHeader("Content-Type", "application/json")
    .setHeader("X-Correlation-ID", "corr-$code")
    .setBody("""{"code":"$code","message":"$message","correlation_id":"corr-$code"}""")

fun ok(status: Int, body: String): MockResponse =
    MockResponse().setResponseCode(status).setHeader("Content-Type", "application/json").setBody(body)

class Client(
    baseUrl: String,
    val store: TokenStore = InMemoryTokenStore()
) {
    val events = SessionEvents()
    val network = Network(baseUrl, store, events)
    val repository = SessionRepository(network, store, "JVM test")
}
