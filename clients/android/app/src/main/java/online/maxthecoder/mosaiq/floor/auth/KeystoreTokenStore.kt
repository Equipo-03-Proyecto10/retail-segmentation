package online.maxthecoder.mosaiq.floor.auth

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import kotlinx.serialization.Serializable
import online.maxthecoder.mosaiq.floor.net.json

/**
 * The tokens, encrypted with an AES-256-GCM key that never leaves the Android
 * Keystore (ADR-0041). The ciphertext sits in `noBackupFilesDir`, and
 * `res/xml/data_extraction_rules.xml` and `backup_rules.xml` exclude it again,
 * so neither a cloud backup nor a device transfer carries a session away. A
 * file that cannot be decrypted, such as one restored onto a new device
 * without its key, reads as signed out.
 */
class KeystoreTokenStore(
    context: Context
) : TokenStore {
    private val file = File(context.noBackupFilesDir, FILE_NAME)
    private val lock = Any()

    override fun read(): Tokens? = synchronized(lock) {
        if (!file.exists()) return null
        runCatching {
            val bytes = file.readBytes()
            val iv = bytes.copyOfRange(0, IV_BYTES)
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, iv))
            val plain = cipher.doFinal(bytes.copyOfRange(IV_BYTES, bytes.size)).decodeToString()
            json.decodeFromString(Stored.serializer(), plain).let { Tokens(it.access, it.refresh) }
        }.getOrElse {
            file.delete()
            null
        }
    }

    override fun write(tokens: Tokens) = synchronized(lock) {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val plain = json.encodeToString(Stored.serializer(), Stored(tokens.accessToken, tokens.refreshToken))
        val sealed = cipher.iv + cipher.doFinal(plain.encodeToByteArray())
        val temporary = File(file.parentFile, "$FILE_NAME.tmp")
        temporary.writeBytes(sealed)
        check(temporary.renameTo(file)) { "The token file could not be replaced." }
    }

    override fun clear() {
        synchronized(lock) { file.delete() }
    }

    private fun key(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec
                .Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(KEY_BITS)
                .build()
        )
        return generator.generateKey()
    }

    @Serializable
    private data class Stored(
        val access: String,
        val refresh: String
    )

    private companion object {
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val KEY_ALIAS = "mosaiq.session.tokens"
        const val FILE_NAME = "session.tokens"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val IV_BYTES = 12
        const val TAG_BITS = 128
        const val KEY_BITS = 256
    }
}
