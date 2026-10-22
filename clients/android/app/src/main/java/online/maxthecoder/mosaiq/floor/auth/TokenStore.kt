package online.maxthecoder.mosaiq.floor.auth

/** The session's two tokens, as auth-service issued them. */
data class Tokens(
    val accessToken: String,
    val refreshToken: String
) {
    // Never print a token, not even by accident in a log line or a crash report.
    override fun toString(): String = "Tokens(redacted)"
}

/** Where the tokens live between requests. */
interface TokenStore {
    fun read(): Tokens?

    fun write(tokens: Tokens)

    fun clear()
}

/** For tests and previews: nothing touches the disk. */
class InMemoryTokenStore(
    initial: Tokens? = null
) : TokenStore {
    @Volatile private var tokens: Tokens? = initial

    override fun read(): Tokens? = tokens

    override fun write(tokens: Tokens) {
        this.tokens = tokens
    }

    override fun clear() {
        tokens = null
    }
}
